"""Aggregates missing_skills across ranked jobs into a skill-gap report.
Single responsibility: turn RankedJob results into an ImprovementReport."""
from __future__ import annotations

from collections import Counter, defaultdict

from api.schemas import ImprovementReport, SkillGap
from models import RankedJob


def build_improvement_report(ranked_jobs: list[RankedJob]) -> ImprovementReport:
    if not ranked_jobs:
        return ImprovementReport(
            top_missing_skills=[],
            average_fit_score=0,
            strongest_matching_skills=[],
            summary="No ranked jobs yet — run a search first.",
        )

    missing_counter: Counter[str] = Counter()
    missing_examples: dict[str, list[str]] = defaultdict(list)
    matching_counter: Counter[str] = Counter()

    for r in ranked_jobs:
        for skill in r.missing_skills:
            key = skill.strip()
            if not key:
                continue
            missing_counter[key] += 1
            if r.job.title not in missing_examples[key]:
                missing_examples[key].append(r.job.title)
        for skill in r.matching_skills:
            key = skill.strip()
            if key:
                matching_counter[key] += 1

    top_missing = [
        SkillGap(skill=skill, frequency=count, sample_jobs=missing_examples[skill][:3])
        for skill, count in missing_counter.most_common(10)
    ]
    strongest_matching = [skill for skill, _ in matching_counter.most_common(8)]
    avg_score = sum(r.fit_score for r in ranked_jobs) / len(ranked_jobs)

    if top_missing:
        summary = (
            f"Across {len(ranked_jobs)} ranked jobs (avg fit {avg_score:.0f}/100), "
            f"the most common gap is '{top_missing[0].skill}' "
            f"(missing from {top_missing[0].frequency} of them). "
            "Closing the top 2-3 gaps below would likely raise your average fit score the most."
        )
    else:
        summary = f"Across {len(ranked_jobs)} ranked jobs, no consistent skill gaps were found — good coverage."

    return ImprovementReport(
        top_missing_skills=top_missing,
        average_fit_score=round(avg_score, 1),
        strongest_matching_skills=strongest_matching,
        summary=summary,
    )
