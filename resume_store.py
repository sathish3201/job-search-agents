"""Per-user filesystem store for original uploaded resume files and
generated (tailored) exports.

One directory per user, at data/resumes/{user_id}/, mirroring store.py's
one-file-per-user ApplicationStore — ownership is structural (which
directory you read/wrote), not a filter condition. No locking needed here
unlike ApplicationStore: each user only ever writes their own original
file (one write per upload, never concurrent) and their own generated
exports (one write per export request), so there's no equivalent of the
ranker thread pool's concurrent-write race that store.py guards against."""
from __future__ import annotations

import os

DEFAULT_DIR = os.path.join(os.path.dirname(__file__), "data", "resumes")


def _user_dir(user_id: int | str) -> str:
    return os.path.join(DEFAULT_DIR, str(user_id))


def _generated_dir(user_id: int | str) -> str:
    return os.path.join(_user_dir(user_id), "generated")


def extension_of(filename: str) -> str:
    idx = filename.rfind(".")
    return filename[idx + 1 :].lower() if idx != -1 else ""


def save_original(user_id: int | str, filename: str, content: bytes) -> str:
    """Stores the original uploaded file, overwriting any previous upload
    for this user (one original per user, matching "one resume per user"
    elsewhere in this app — see agents/resume_vector_store.py's identical
    choice). Returns the extension stored under."""
    ext = extension_of(filename)
    user_dir = _user_dir(user_id)
    os.makedirs(user_dir, exist_ok=True)
    with open(os.path.join(user_dir, f"original.{ext}"), "wb") as f:
        f.write(content)
    return ext


def load_original(user_id: int | str) -> tuple[bytes, str] | None:
    """Returns (content, ext), or None if this user has no original file
    on record (never uploaded one, or uploaded a .txt/.md — see
    api/routers/profile.py, which only calls save_original for pdf/docx)."""
    user_dir = _user_dir(user_id)
    if not os.path.isdir(user_dir):
        return None
    for entry in os.listdir(user_dir):
        if entry.startswith("original."):
            with open(os.path.join(user_dir, entry), "rb") as f:
                return f.read(), entry.rsplit(".", 1)[1]
    return None


def save_generated(user_id: int | str, dedupe_key: str, fmt: str, content: bytes) -> str:
    """Caches the most recently exported tailored resume for one job —
    overwritten on each export, not versioned/history-kept (a single
    most-recent export per (user, job, format) is all the preview/export
    endpoints need)."""
    gen_dir = _generated_dir(user_id)
    os.makedirs(gen_dir, exist_ok=True)
    safe_key = dedupe_key.replace("/", "_").replace(":", "_")
    path = os.path.join(gen_dir, f"{safe_key}.{fmt}")
    with open(path, "wb") as f:
        f.write(content)
    return path


def load_generated(user_id: int | str, dedupe_key: str, fmt: str) -> bytes | None:
    safe_key = dedupe_key.replace("/", "_").replace(":", "_")
    path = os.path.join(_generated_dir(user_id), f"{safe_key}.{fmt}")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()
