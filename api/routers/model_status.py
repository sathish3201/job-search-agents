"""Server-side LLM-backend reachability check for the frontend's status
badge. Same purpose and pattern as the portfolio site's own
GET /api/model-status route: the browser can't check the tunnel's health
directly (CORS — the Termux llama-server doesn't send
Access-Control-Allow-Origin, confirmed by a real cross-origin OPTIONS
request returning no CORS headers at all), but a server-to-server request
from this API to the tunnel isn't subject to browser CORS, so proxying the
check through here is what actually works."""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["model-status"])


@router.get("/model-status")
def model_status():
    service_url = os.getenv("OLLAMA_SERVICE_URL")
    if not service_url:
        # No tunnel configured — this deployment is using a direct
        # provider (OpenAI/Anthropic/Groq/local Ollama) instead, which
        # doesn't have a single cheap /health route to check generically.
        return {"online": None, "detail": "No OLLAMA_SERVICE_URL configured"}

    try:
        resp = httpx.get(f"{service_url.rstrip('/')}/health", timeout=8)
        resp.raise_for_status()
        return {"online": True, "detail": resp.json()}
    except Exception as e:
        return {"online": False, "detail": str(e)}
