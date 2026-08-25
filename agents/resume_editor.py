"""Deterministic verification layer for interactive resume edits — direct
extension of agents/ats_checker.py's fabrication-guard philosophy
("verify deterministically, never trust the model's compliance claim") to
arbitrary section edits, not just headline/summary.

Called from agents/tailor_agent.py's tool implementations. Never applies
an edit that fails a guard silently:
  - EditRejected: a structural problem (bad section_id, empty result) —
    fed back to the agent as an error to retry or explain.
  - NeedsClarification: the draft text contains a number/metric not found
    anywhere in the candidate's real resume (e.g. "reduced costs by 80%").
    Earlier this only silently stripped such sentences
    (agents/ats_checker.py's _strip_fabricated_numbers, still used as-is
    by the non-interactive tailor_resume_for_target path, which has no
    chat turn to ask a question in). For the interactive chat agent,
    silent stripping is worse UX than just asking the user for the real
    number — same safety goal (never let an invented figure land in the
    resume), reached by asking instead of guessing. NOT applied
    automatically here — the edit is NOT made; the agent asks the user
    and retries the tool call once a real number is given in a later
    turn."""
from __future__ import annotations

from agents.ats_checker import find_unsupported_numbers
from models import ResumeSection, TailoredDraft


class EditRejected(Exception):
    """Raised when a proposed edit fails a structural deterministic check."""


class NeedsClarification(Exception):
    """Raised when a proposed edit contains a number/metric not supported
    by the candidate's real resume text — the agent should ask the user
    for the real figure rather than the edit being silently stripped or
    silently applied."""

    def __init__(self, unsupported_numbers: list[str]):
        self.unsupported_numbers = unsupported_numbers
        super().__init__(
            f"These figures aren't found anywhere in your resume: {', '.join(unsupported_numbers)}. "
            "Ask the user what the real number is before writing this."
        )


def _find_section(draft: TailoredDraft, section_id: str) -> ResumeSection:
    for section in draft.sections:
        if section.section_id == section_id:
            return section
    raise EditRejected(
        f"No section with id {section_id!r} exists in the current draft. "
        "Call list_sections first to see valid ids."
    )


def apply_section_edit(draft: TailoredDraft, section_id: str, new_text: str, supported_text: str) -> None:
    """Mutates draft.sections in place.
    1. section_id must exist (defends against a tool-calling model
       hallucinating an id it never got from list_sections()).
    2. new_text is checked (not stripped) for numbers unsupported by
       supported_text — see NeedsClarification above. supported_text is
       resume_raw_text PLUS the user's own chat messages this session
       (see agents/tailor_agent.py's run_turn) — a number the user
       explicitly typed in the conversation ("actually it was 35%") is
       exactly the kind of human-confirmed ground truth this feature asks
       for, not a fabrication to keep re-questioning. The edit is only
       applied once new_text passes this check clean."""
    section = _find_section(draft, section_id)
    unsupported = find_unsupported_numbers(new_text, supported_text)
    if unsupported:
        raise NeedsClarification(unsupported)
    if not new_text.strip():
        raise EditRejected("The proposed text was empty.")
    section.text = new_text.strip()


def add_bullet(draft: TailoredDraft, after_section_id: str, text: str, supported_text: str) -> str:
    """Inserts a new experience_bullet section immediately after an
    existing one. Same clarification-first guard as apply_section_edit.
    Returns the new section's id."""
    anchor = _find_section(draft, after_section_id)
    unsupported = find_unsupported_numbers(text, supported_text)
    if unsupported:
        raise NeedsClarification(unsupported)
    if not text.strip():
        raise EditRejected("The proposed bullet was empty.")

    new_id = f"{after_section_id}-added-{sum(1 for s in draft.sections if s.section_id.startswith(f'{after_section_id}-added-'))}"
    new_section = ResumeSection(
        section_id=new_id, section_type="experience_bullet", text=text.strip(), order=anchor.order + 1
    )
    insert_at = draft.sections.index(anchor) + 1
    draft.sections.insert(insert_at, new_section)
    _renumber(draft)
    return new_id


def remove_section(draft: TailoredDraft, section_id: str) -> None:
    section = _find_section(draft, section_id)
    draft.sections.remove(section)
    _renumber(draft)


def _renumber(draft: TailoredDraft) -> None:
    for i, section in enumerate(draft.sections):
        section.order = i
