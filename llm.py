"""Picks an LLM backend at runtime based on whichever API key/connector is present.
Priority: OLLAMA_SERVICE_URL (your ngrok-tunneled ollama-service) > Anthropic > OpenAI
> Groq > direct local Ollama fallback. This mirrors the "dynamic connector" pattern
used for job sources — nothing errors if a given backend isn't configured, it just
falls through to the next one."""
from __future__ import annotations

import os


def get_llm(temperature: float = 0, max_tokens: int = 512):
    # max_tokens caps runaway generations — small local models occasionally hit
    # a repetition loop (e.g. emitting the same skill hundreds of times) on
    # poor-fit/out-of-domain prompts; capping length bounds the damage and cost.

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
        )

    if os.getenv("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model="claude-sonnet-4-5", temperature=temperature, max_tokens=max_tokens)

    if os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model="gpt-4o-mini", temperature=temperature, max_tokens=max_tokens)

    if os.getenv("GROQ_API_KEY"):
        from langchain_groq import ChatGroq

        return ChatGroq(model="llama-3.3-70b-versatile", temperature=temperature, max_tokens=max_tokens)

    from langchain_ollama import ChatOllama

    model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    return ChatOllama(model=model, temperature=temperature, num_predict=max_tokens)
