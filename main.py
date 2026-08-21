"""CLI entrypoint for the job-search agent pipeline.

Usage:
    python main.py                       # safe mode: search, rank, track, draft report
    python main.py --mode automation     # ALSO opens a browser to apply profile edits
                                          # (asks for explicit confirmation, see agents/automation.py)
"""
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

load_dotenv(override=True)  # this project's .env wins over any stray system env vars

from agents.graph import build_graph, PipelineState
from llm import get_llm
from profile_loader import load_resume_text, parse_profile
from reports import write_report
from sources import active_sources


def main():
    parser = argparse.ArgumentParser(description="LangGraph job-search agent pipeline")
    parser.add_argument(
        "--mode",
        choices=["safe", "automation"],
        default="safe",
        help="safe (default): writes suggestions to a report file. "
        "automation: also logs into LinkedIn/Naukri and applies edits directly — "
        "HIGH RISK, see agents/automation.py.",
    )
    args = parser.parse_args()

    print(f"Mode: {args.mode}")
    sources = active_sources()
    print(f"Active job sources: {[s.name for s in sources] or 'NONE — add API keys to .env'}")

    llm = get_llm()
    resume_text = load_resume_text()
    print("Parsing candidate profile from resume...")
    profile = parse_profile(resume_text, llm)
    print(f"Profile loaded: {profile.name} — {profile.headline}")

    graph = build_graph()
    state = PipelineState(
        query=os.getenv("JOB_SEARCH_QUERY", "Full Stack Developer"),
        location=os.getenv("JOB_SEARCH_LOCATION", ""),
        remote_ok=os.getenv("JOB_SEARCH_REMOTE_OK", "true").lower() == "true",
        profile=profile,
    )

    print("Running pipeline: search -> rank -> track -> draft profile updates...")
    result = graph.invoke(state)

    ranked_jobs = result["ranked_jobs"]
    profile_drafts = result["profile_drafts"]
    print(f"\nFound {len(result['raw_jobs'])} jobs, ranked {len(ranked_jobs)}, "
          f"{len(result['new_applications'])} newly added to tracker.")

    report_path = write_report(ranked_jobs, profile_drafts)
    print(f"Report written to: {report_path}")

    if args.mode == "automation":
        if not profile_drafts:
            print("No profile draft generated this run — nothing to apply.")
            return
        from agents.automation import apply_linkedin_profile_update

        draft = profile_drafts[0]
        apply_linkedin_profile_update(draft)


if __name__ == "__main__":
    main()
