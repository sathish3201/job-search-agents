"""FastAPI entrypoint. Run with: uvicorn api.app:app --reload --port 8000"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv(override=True)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import applications, improvement, pipeline, profile

app = FastAPI(title="Job Search Agent API", version="1.0")

# CORS_ALLOWED_ORIGINS is a comma-separated list, e.g.
# "https://job-search-agent-web-5zfq.onrender.com,http://localhost:5173"
_extra_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://job-search-agent-web-5zfq.onrender.com",
        *_extra_origins,
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pipeline.router)
app.include_router(applications.router)
app.include_router(profile.router)
app.include_router(improvement.router)


@app.get("/health")
def health():
    return {"status": "ok"}
