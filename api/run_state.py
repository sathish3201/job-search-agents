"""In-memory holder for the most recent pipeline run's results, plus its status.
Single responsibility: hold state; it doesn't know how to run the pipeline
(that's api/pipeline_runner.py) or what to do with the data (that's the routers).
Fine for a single-process deployment; if this ever needs to survive restarts or
run multi-process, swap this for a small JSON/SQLite-backed store behind the
same interface."""
from __future__ import annotations

import threading

from models import CandidateProfile, ProfileDraft, RankedJob


class RunState:
    def __init__(self):
        self._lock = threading.Lock()
        self.status: str = "idle"
        self.message: str = ""
        self.ranked_jobs: list[RankedJob] = []
        self.profile_drafts: list[ProfileDraft] = []
        self.profile: CandidateProfile | None = None
        self.new_applications_count: int = 0

    def set_running(self) -> None:
        with self._lock:
            self.status = "running"
            self.message = "Pipeline is running..."

    def set_progress(self, stage: str, done: int = 0, total: int = 0) -> None:
        """Called from inside the pipeline to report where it currently is —
        ranking one job at a time via a slow remote LLM is the long pole, so
        without this the status endpoint just says "running" for minutes with
        no indication anything is happening."""
        with self._lock:
            if total:
                self.message = f"{stage}: {done}/{total}"
            else:
                self.message = stage

    def set_done(
        self,
        ranked_jobs: list[RankedJob],
        profile_drafts: list[ProfileDraft],
        profile: CandidateProfile,
        new_applications_count: int,
    ) -> None:
        with self._lock:
            self.status = "done"
            self.message = f"Completed: {len(ranked_jobs)} jobs ranked."
            self.ranked_jobs = ranked_jobs
            self.profile_drafts = profile_drafts
            self.profile = profile
            self.new_applications_count = new_applications_count

    def set_error(self, message: str) -> None:
        with self._lock:
            self.status = "error"
            self.message = message


run_state = RunState()
