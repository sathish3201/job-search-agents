"""Holder for the most recent pipeline run's results, plus its status.
Single responsibility: hold state; it doesn't know how to run the pipeline
(that's api/pipeline_runner.py) or what to do with the data (that's the
routers).

The completed-run snapshot (ranked_jobs, ats_passed_jobs, profile_drafts,
profile, new_applications_count) is persisted to a JSON file on every
set_done() and reloaded on process start, so a Render free-tier restart
(idle spin-down, redeploy) doesn't silently blank the dashboard back to
"no jobs yet" — the frontend was otherwise indistinguishable from a
never-run pipeline after any restart, even though data/applications.json
and the SQLite cache both already survived restarts fine. status/message/
live_jobs stay in-memory only on purpose: they describe "is a run
happening right now in this process", which is never true immediately
after a fresh boot, so persisting them would just replay a stale
"running"/"error" message from before the restart."""
from __future__ import annotations

import json
import os
import threading

from models import CandidateProfile, ProfileDraft, RankedJob

DEFAULT_SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "last_run.json")


class RunState:
    def __init__(self, snapshot_path: str = DEFAULT_SNAPSHOT_PATH):
        self._lock = threading.Lock()
        self._snapshot_path = snapshot_path
        self.status: str = "idle"
        self.message: str = ""
        # ats_passed_jobs is what the frontend displays — jobs that cleared
        # both MIN_FIT_SCORE (LLM judgment) and ATS_FRONTEND_THRESHOLD
        # (deterministic keyword match). ranked_jobs is kept too since it's
        # the superset used for the improvement report and application
        # tracker, which care about LLM fit regardless of ATS keyword match.
        self.ranked_jobs: list[RankedJob] = []
        self.ats_passed_jobs: list[RankedJob] = []
        self.profile_drafts: list[ProfileDraft] = []
        self.profile: CandidateProfile | None = None
        self.new_applications_count: int = 0
        # Jobs pushed here the instant each one clears both the fit and ATS
        # thresholds, mid-run — lets the frontend show results incrementally
        # as they're found instead of a single reveal when the whole run
        # finishes. Cleared at the start of each run, superseded by
        # ats_passed_jobs (the authoritative final list) once done.
        self.live_jobs: list[RankedJob] = []
        self._load_snapshot()

    def set_running(self) -> None:
        with self._lock:
            self.status = "running"
            self.message = "Pipeline is running..."
            self.live_jobs = []

    def add_live_job(self, job: RankedJob) -> None:
        with self._lock:
            self.live_jobs.append(job)

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
        ats_passed_jobs: list[RankedJob],
        profile_drafts: list[ProfileDraft],
        profile: CandidateProfile,
        new_applications_count: int,
    ) -> None:
        with self._lock:
            self.status = "done"
            self.message = f"Completed: {len(ats_passed_jobs)} jobs passed ATS check (of {len(ranked_jobs)} ranked)."
            self.ranked_jobs = ranked_jobs
            self.ats_passed_jobs = ats_passed_jobs
            self.profile_drafts = profile_drafts
            self.profile = profile
            self.new_applications_count = new_applications_count
            self._save_snapshot()

    def set_error(self, message: str) -> None:
        with self._lock:
            self.status = "error"
            self.message = message

    def _save_snapshot(self) -> None:
        """Writes the completed-run fields only — status/message/live_jobs
        are deliberately excluded (see class docstring). Called with
        self._lock already held by set_done(); not safe to call standalone."""
        os.makedirs(os.path.dirname(self._snapshot_path), exist_ok=True)
        snapshot = {
            "ranked_jobs": [j.model_dump(mode="json") for j in self.ranked_jobs],
            "ats_passed_jobs": [j.model_dump(mode="json") for j in self.ats_passed_jobs],
            "profile_drafts": [d.model_dump(mode="json") for d in self.profile_drafts],
            "profile": self.profile.model_dump(mode="json") if self.profile else None,
            "new_applications_count": self.new_applications_count,
        }
        with open(self._snapshot_path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)

    def _load_snapshot(self) -> None:
        """Best-effort restore on process start. A missing or corrupt
        snapshot file just means "no prior run to show" — the same state a
        fresh boot already has — so this never raises past __init__."""
        if not os.path.exists(self._snapshot_path):
            return
        try:
            with open(self._snapshot_path, encoding="utf-8") as f:
                snapshot = json.load(f)
            self.ranked_jobs = [RankedJob.model_validate(j) for j in snapshot["ranked_jobs"]]
            self.ats_passed_jobs = [RankedJob.model_validate(j) for j in snapshot["ats_passed_jobs"]]
            self.profile_drafts = [ProfileDraft.model_validate(d) for d in snapshot["profile_drafts"]]
            self.profile = (
                CandidateProfile.model_validate(snapshot["profile"]) if snapshot["profile"] else None
            )
            self.new_applications_count = snapshot["new_applications_count"]
            if self.ranked_jobs or self.profile:
                self.status = "done"
                self.message = (
                    f"Completed: {len(self.ats_passed_jobs)} jobs passed ATS check "
                    f"(of {len(self.ranked_jobs)} ranked). [restored from last run]"
                )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"[run_state] failed to restore snapshot, starting fresh: {e}", flush=True)


run_state = RunState()
