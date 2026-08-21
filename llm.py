"""Picks an LLM backend at runtime based on whichever API key/connector is present.
Priority: OLLAMA_SERVICE_URL (your ngrok-tunneled ollama-service) > Anthropic > OpenAI
> Groq > direct local Ollama fallback. This mirrors the "dynamic connector" pattern
used for job sources — nothing errors if a given backend isn't configured, it just
falls through to the next one."""
from __future__ import annotations

import os


REQUEST_TIMEOUT_SECONDS = 45  # hard cap per LLM call — see note below


def get_llm(temperature: float = 0, max_tokens: int = 512):
    # max_tokens caps runaway generations — small local models occasionally hit
    # a repetition loop (e.g. emitting the same skill hundreds of times) on
    # poor-fit/out-of-domain prompts; capping length bounds the damage and cost.
    #
    # timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0 are both load-bearing: a
    # free ngrok tunnel occasionally stalls a TCP connection without ever
    # closing or erroring it. Without a client-side timeout, langchain-openai's
    # default (much longer, and retried on top of that) means one bad
    # connection can hang the whole ranking loop for many minutes with zero
    # log output, indistinguishable from the process being dead. Better to
    # fail one job's ranking fast and let the run continue.

    # Your ollama-service (FastAPI wrapper exposing an OpenAI-compatible
    # /v1/chat/completions, tunneled via ngrok) — reuses ChatOpenAI's client
    # since the endpoint shape matches, just pointed at a different base_url.
    if os.getenv("OLLAMA_SERVICE_URL"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=os.getenv("OLLAMA_SERVICE_MODEL", "phi3:mini"),
            temperature=temperature,
            max_tokens=max_tokens,
            base_url=os.getenv("OLLAMA_SERVICE_URL").rstrip("/") + "/v1",
            api_key=os.getenv("OLLAMA_SERVICE_API_KEY", "sk-local-placeholder"),
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )

    if os.getenv("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model="claude-sonnet-4-5",
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )

    if os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model="gpt-4o-mini",
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )

    if os.getenv("GROQ_API_KEY"):
        from langchain_groq import ChatGroq

        return ChatGroq(
            model="llama-3.3-70b-versatile",
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )

    from langchain_ollama import ChatOllama

    model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    return ChatOllama(
        model=model,
        temperature=temperature,
        num_predict=max_tokens,
        client_kwargs={"timeout": REQUEST_TIMEOUT_SECONDS},
    )
