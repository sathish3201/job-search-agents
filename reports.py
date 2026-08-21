"""Writes human-readable run reports: ranked jobs + profile-update suggestions.
This is the "safe mode" output — a file you read and act on yourself."""
from __future__ import annotations

import os
from datetime import datetime

from models import ProfileDraft, RankedJob

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "data", "reports")


def write_report(ranked_jobs: list[RankedJob], profile_drafts: list[ProfileDraft]) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    path = os.path.join(REPORTS_DIR, f"report_{stamp}.md")

    lines = [f"# Job Search Report — {stamp}\n"]

    lines.append("## Top Ranked Jobs\n")
    for r in sorted(ranked_jobs, key=lambda x: x.fit_score, reverse=True)[:20]:
        lines.append(f"### {r.job.title} @ {r.job.company} — fit {r.fit_score}/100")
        lines.append(f"- Source: {r.job.source} | Location: {r.job.location} | Remote: {r.job.remote}")
        lines.append(f"- URL: {r.job.url}")
        lines.append(f"- Matching skills: {', '.join(r.matching_skills) or 'n/a'}")
        lines.append(f"- Missing skills: {', '.join(r.missing_skills) or 'n/a'}")
        lines.append(f"- Suggested pitch: {r.tailored_pitch}\n")

    if profile_drafts:
        lines.append("## Suggested Profile Updates (review before pasting anywhere)\n")
        for d in profile_drafts:
            lines.append(f"### {d.platform.title()}")
            lines.append(f"**New headline:** {d.headline}\n")
            lines.append(f"**New summary:**\n{d.summary}\n")
            lines.append(f"**Why:** {d.reasoning}")
            lines.append(f"**Triggered by:** {d.based_on_trend}\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return path
