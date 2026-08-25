"""Stateful, tool-calling resume-editing agent for the interactive
tailoring dialog. One agent turn = one synchronous HTTP request (see
api/routers/tailor_chat.py) — no streaming, matching this repo's
synchronous .invoke() culture in llm.py.

Genuinely new infrastructure: agents/graph.py's StateGraph is a fixed
linear pipeline with no tool-calling anywhere else in this codebase. This
is its own separate compiled graph/loop, never wired into
build_graph()/PipelineState.

Two interchangeable turn-execution strategies, selected by backend
capability (see _select_backend below) — Single Responsibility per
strategy, Open/Closed for adding a third strategy later, and the caller
(run_turn) depends only on the AgentBackend interface, never on which
concrete strategy is active (Dependency Inversion):

- NativeToolCallingBackend: langgraph.prebuilt.create_react_agent +
  bind_tools — used for backends confirmed to support real tool-calling
  (Anthropic, OpenAI, Groq).
- JsonModeBackend: a hand-rolled loop that prompts the model to respond
  with one JSON action object, parsed deterministically and dispatched to
  the same tool functions — used for backends that don't reliably honor
  bind_tools. Confirmed necessary by direct testing: this project's
  actual configured backend (phi3:mini via the OLLAMA_SERVICE_URL tunnel)
  returned zero tool_calls even when explicitly instructed to use a tool
  ("What is 5 + 3? Use the add tool." -> plain-text "5 + 3 is 8.", no
  tool_calls). Every real edit in this feature depends on a tool actually
  firing, so this fallback is not optional polish — it's the only path
  that currently works end-to-end with this project's default backend.

Every tool implementation independently re-validates its own arguments
against the real draft state (see agents/resume_editor.py's EditRejected)
rather than trusting either strategy's output was well-formed — same
"verify deterministically, never trust compliance" philosophy as
agents/ats_checker.py's fabrication guard."""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod

from langchain_core.tools import tool

from agents.ats_checker import check_keyword_presence, extract_keywords
from agents.resume_editor import EditRejected, NeedsClarification, add_bullet, apply_section_edit, remove_section
from llm import get_llm
from models import ChatMessage, TailoredDraft

_SYSTEM_PROMPT = """You are a resume-editing assistant helping a candidate \
tailor their resume for one specific job. You can only edit text that is \
already truthfully supported by the candidate's real resume — never invent \
experience, metrics, percentages, employers, or skills the candidate does \
not already have. If the user asks for something not truthfully supportable, \
say so plainly instead of inventing it.

If edit_section or add_bullet tells you a figure isn't supported by the \
resume ("Needs clarification"), do NOT retry with a different invented \
number and do NOT drop the claim silently — ask the user what the real \
number is (e.g. "What % did that actually reduce costs by?") and wait for \
their reply. Once they give you a real figure, call the tool again with \
their exact number.

Before editing a section you haven't seen yet, call list_sections to find \
its exact section_id — never guess an id.

After making any edit, call rescore to report the current ATS score \
accurately — never state a score from memory or estimate one.

Keep your replies short and concrete: say what you changed and why."""

# Backends confirmed to honor bind_tools reliably (native OpenAI-style
# function-calling). Everything else (ChatOllama, and the
# OLLAMA_SERVICE_URL tunnel — which is also a ChatOpenAI instance
# pointed at a small local model, so class name alone can't distinguish
# it from real OpenAI) uses the JSON-mode fallback. See llm.py: only
# ANTHROPIC_API_KEY and GROQ_API_KEY route to a backend that is
# unambiguously NOT the local tunnel.
_NATIVE_TOOL_CALLING_ENV_VARS = ("ANTHROPIC_API_KEY", "GROQ_API_KEY")


def _to_lc_tuple(msg: ChatMessage) -> tuple[str, str]:
    role = "assistant" if msg.role == "agent" else "user"
    return (role, msg.content)


def _build_supported_text(resume_raw_text: str, messages: list[ChatMessage], user_message: str) -> str:
    """resume_raw_text plus everything the USER has typed this session
    (not the agent's own replies, which could otherwise let the agent
    launder a fabricated number through its own prior turn) — see
    agents/resume_editor.py's apply_section_edit for why a user-stated
    number counts as supported ground truth."""
    user_turns = [m.content for m in messages if m.role == "user"]
    return "\n".join([resume_raw_text, *user_turns, user_message])


def _make_tool_functions(draft: TailoredDraft, job_description: str, resume_raw_text: str, supported_text: str):
    """Plain Python callables (not @tool-wrapped) shared by both backend
    strategies — closures over THIS turn's draft, so one session's call
    can never leak into a concurrent session's draft (each run_turn()
    call builds a fresh set). supported_text = resume_raw_text + this
    session's user chat turns (see run_turn) — see resume_editor.py's
    apply_section_edit for why the latter counts as supported ground
    truth, not fabrication."""

    def list_sections() -> str:
        lines = [f"{s.section_id} ({s.section_type}): {s.text[:80]}" for s in draft.sections]
        return "\n".join(lines) if lines else "No sections in the draft."

    def edit_section(section_id: str, new_text: str) -> str:
        try:
            apply_section_edit(draft, section_id, new_text, supported_text)
        except NeedsClarification as e:
            return f"Needs clarification: {e}"
        except EditRejected as e:
            return f"Edit rejected: {e}"
        return f"Updated section {section_id}."

    def add_bullet_fn(after_section_id: str, text: str) -> str:
        try:
            new_id = add_bullet(draft, after_section_id, text, supported_text)
        except NeedsClarification as e:
            return f"Needs clarification: {e}"
        except EditRejected as e:
            return f"Bullet rejected: {e}"
        return f"Added new bullet as section {new_id}."

    def remove_section_fn(section_id: str) -> str:
        try:
            remove_section(draft, section_id)
        except EditRejected as e:
            return f"Could not remove: {e}"
        return f"Removed section {section_id}."

    def rescore() -> str:
        keywords = extract_keywords(job_description)
        combined_text = " ".join(s.text for s in draft.sections)
        found, missing = check_keyword_presence(keywords, combined_text)
        draft.ats_score = round(100 * len(found) / len(keywords)) if keywords else 100
        draft.keywords_found, draft.keywords_missing = found, missing
        return f"ATS score is now {draft.ats_score}/100. Missing keywords: {', '.join(missing) or 'none'}."

    return {
        "list_sections": list_sections,
        "edit_section": edit_section,
        "add_bullet": add_bullet_fn,
        "remove_section": remove_section_fn,
        "rescore": rescore,
    }


_TOOL_ARG_SPECS = {
    "list_sections": [],
    "edit_section": ["section_id", "new_text"],
    "add_bullet": ["after_section_id", "text"],
    "remove_section": ["section_id"],
    "rescore": [],
}


class AgentBackend(ABC):
    """One agent turn: given chat history + a new user message, produce a
    reply and (as a side effect) mutate `draft` via tool functions. Both
    implementations below satisfy this same contract — run_turn() depends
    only on this interface, never on which concrete strategy is active."""

    @abstractmethod
    def run_turn(
        self,
        draft: TailoredDraft,
        messages: list[ChatMessage],
        user_message: str,
        job_description: str,
        resume_raw_text: str,
    ) -> str:
        """Returns the agent's reply text. Mutates draft in place via tool calls."""


def _get_native_tool_calling_llm(max_tokens: int = 800):
    """Deliberately does NOT call llm.py's get_llm() — that function's
    backend priority is OLLAMA_SERVICE_URL > Anthropic > OpenAI > Groq >
    local Ollama (see llm.py's own docstring), so as long as
    OLLAMA_SERVICE_URL is set (true for this project's normal local/dev
    setup), get_llm() always returns the tunnel client regardless of
    whether a Groq/Anthropic key is also present — confirmed by a real
    test: bind_tools() against get_llm()'s result still hit the ngrok
    tunnel even with GROQ_API_KEY set. This function instead directly
    instantiates whichever backend _select_backend() decided supports
    real tool-calling, bypassing that priority order entirely."""
    import os

    if os.getenv("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model="claude-sonnet-4-5", temperature=0, max_tokens=max_tokens, max_retries=0)

    from langchain_groq import ChatGroq

    return ChatGroq(model="llama-3.3-70b-versatile", temperature=0, max_tokens=max_tokens, max_retries=0)


class NativeToolCallingBackend(AgentBackend):
    """langgraph.prebuilt.create_react_agent + bind_tools — for backends
    confirmed to honor real tool-calling (Anthropic, Groq)."""

    def run_turn(self, draft, messages, user_message, job_description, resume_raw_text) -> str:
        from langgraph.prebuilt import create_react_agent

        supported_text = _build_supported_text(resume_raw_text, messages, user_message)
        fns = _make_tool_functions(draft, job_description, resume_raw_text, supported_text)
        lc_tools = [
            tool(fns["list_sections"], name="list_sections"),
            tool(fns["edit_section"], name="edit_section"),
            tool(fns["add_bullet"], name="add_bullet"),
            tool(fns["remove_section"], name="remove_section"),
            tool(fns["rescore"], name="rescore"),
        ]
        llm = _get_native_tool_calling_llm()
        agent = create_react_agent(llm, lc_tools, prompt=_SYSTEM_PROMPT)

        lc_messages = [_to_lc_tuple(m) for m in messages]
        lc_messages.append(("user", user_message))
        result = agent.invoke({"messages": lc_messages})
        reply = result["messages"][-1].content
        return reply if isinstance(reply, str) else str(reply)


_JSON_MODE_INSTRUCTIONS = """You must respond with EXACTLY ONE JSON object, \
nothing else — no prose before or after it. The JSON object has this shape:

{{"action": "<one of: list_sections, edit_section, add_bullet, remove_section, rescore, reply>", "args": {{...}}, "message": "<what you'll say to the user>"}}

Valid actions and their required args:
- list_sections: {{}} — look up section ids before editing one you haven't seen
- edit_section: {{"section_id": "...", "new_text": "..."}}
- add_bullet: {{"after_section_id": "...", "text": "..."}}
- remove_section: {{"section_id": "..."}}
- rescore: {{}} — call after any edit before reporting a score
- reply: {{}} — use this when you're just responding, not calling a tool (e.g. after \
list_sections/rescore told you what you needed, or you're declining an \
untruthful request)

Current sections in the draft:
{sections}

Respond with the single JSON object now."""


class JsonModeBackend(AgentBackend):
    """Hand-rolled action loop for backends that don't reliably honor
    bind_tools (this project's default — see the module docstring for the
    confirmed failure this addresses). Prompts the model to name one
    action as JSON, parses it deterministically, dispatches to the same
    tool functions NativeToolCallingBackend uses. Loops up to
    max_steps times so the model can call list_sections/rescore before its
    final reply, same shape as a real tool-calling loop, just without
    relying on the model's native function-calling support."""

    max_steps = 4

    def run_turn(self, draft, messages, user_message, job_description, resume_raw_text) -> str:
        supported_text = _build_supported_text(resume_raw_text, messages, user_message)
        fns = _make_tool_functions(draft, job_description, resume_raw_text, supported_text)
        llm = get_llm(max_tokens=500)

        history = [_to_lc_tuple(m) for m in messages]
        history.append(("user", user_message))

        last_message = ""
        for _ in range(self.max_steps):
            sections_preview = fns["list_sections"]()
            prompt = _SYSTEM_PROMPT + "\n\n" + _JSON_MODE_INSTRUCTIONS.format(sections=sections_preview)
            conversation = [("system", prompt)] + history
            resp = llm.invoke(conversation)
            raw = resp.content if isinstance(resp.content, str) else str(resp.content)

            parsed = _extract_json_object(raw)
            if parsed is None:
                # Model didn't follow the format — treat its raw text as
                # the final reply rather than looping forever on
                # unparseable output.
                return raw.strip()

            action = parsed.get("action", "reply")
            args = parsed.get("args", {}) or {}
            message = parsed.get("message", "")

            if action == "reply" or action not in fns:
                return message or raw.strip()

            expected_args = _TOOL_ARG_SPECS.get(action, [])
            missing = [a for a in expected_args if a not in args]
            if missing:
                tool_result = f"Action {action!r} rejected: missing required args {missing}."
            else:
                try:
                    tool_result = fns[action](**{k: args[k] for k in expected_args})
                except TypeError as e:
                    tool_result = f"Action {action!r} rejected: {e}"

            history.append(("assistant", f"[Called {action}({args})] {message}".strip()))
            history.append(("user", f"[Tool result] {tool_result}"))
            last_message = message or tool_result

        return last_message or "I've made the requested changes."


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json_object(text: str) -> dict | None:
    """Models in JSON-mode sometimes wrap the object in a code fence or add
    stray text around it despite instructions — extract the first
    brace-to-brace span and parse that, rather than requiring the whole
    response to be pure JSON."""
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        result = json.loads(match.group(0))
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


def _select_backend() -> AgentBackend:
    import os

    if any(os.getenv(var) for var in _NATIVE_TOOL_CALLING_ENV_VARS):
        return NativeToolCallingBackend()
    return JsonModeBackend()


def run_turn(
    draft: TailoredDraft,
    messages: list[ChatMessage],
    user_message: str,
    target_section_id: str | None,
    job_description: str,
    resume_raw_text: str,
) -> tuple[str, TailoredDraft]:
    """Entry point used by api/routers/tailor_chat.py. Selects a backend
    strategy (see _select_backend), runs one turn, returns (reply,
    mutated draft — mutated in place, returned for call-site clarity)."""
    if target_section_id:
        user_message = f"[Targeting section: {target_section_id}] {user_message}"

    backend = _select_backend()
    reply = backend.run_turn(draft, messages, user_message, job_description, resume_raw_text)
    return reply, draft
