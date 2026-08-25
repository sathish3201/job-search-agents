"""Bootstraps a TailoredDraft (structured, editable resume state) from a
CandidateProfile and target job — the entry point into the interactive
tailoring dialog (see api/routers/tailor_chat.py's /start endpoint).

Headline/summary are seeded from the EXISTING deterministic
tailor_resume_for_target() (agents/ats_checker.py) — reused, not
reimplemented. Everything else (experience bullets, skills, education)
starts as a verbatim split of profile.resume_raw_text into sections; the
chat agent (agents/tailor_agent.py) is what edits those from here. This
function never invents content — it only reorganizes what's already in
the resume text into addressable, editable units.
"""
from __future__ import annotations

import re

from agents.ats_checker import check_keyword_presence, extract_keywords, tailor_resume_for_target
from models import CandidateProfile, JobListing, ResumeSection, TailoredDraft

_BULLET_RE = re.compile(r"^\s*[-*•]\s+(.*)$")
_HEADING_RE = re.compile(r"^\s*#{1,3}\s+(.*)$")  # markdown ## headings
_BOLD_LINE_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*$")  # a line that's entirely **bold** (sub-heading)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")  # [text](url) -> text
_MD_EMPHASIS_RE = re.compile(r"\*{1,2}([^*]+)\*{1,2}")  # **bold**/*italic* -> plain


def _strip_markdown(text: str) -> str:
    """Renders (PDF/DOCX) treat section text as plain text, not markdown —
    without this, raw markdown syntax from the source resume (link
    brackets, bold asterisks) leaked into the exported document verbatim
    (confirmed via a real generated PDF during Stage 4 verification)."""
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_EMPHASIS_RE.sub(r"\1", text)
    return text.strip()


def _split_into_lines(resume_raw_text: str, skip_lines_lower: set[str]) -> list[ResumeSection]:
    """Splits raw resume text line-by-line into ResumeSections. Deliberately
    simple/heuristic (markdown-heading, bullet-marker, or plain-paragraph
    classification) rather than a full markdown parser — good enough to
    make every line independently addressable for click-to-target and
    chat-driven edits, which is the actual requirement; it doesn't need to
    perfectly reconstruct the original document's semantic structure.

    skip_lines_lower: lines matching profile.headline/summary (already
    captured as their own dedicated sections by build_initial_draft) are
    skipped here — a resume's raw text always contains its own headline
    near the top, so without this the rendered draft duplicated the name/
    headline block (confirmed via a real generated PDF/DOCX during Stage 4
    verification)."""
    sections: list[ResumeSection] = []
    order = 0
    for raw_line in resume_raw_text.splitlines():
        line = raw_line.strip()
        if not line or line in ("---", "***"):
            continue

        bullet_match = _BULLET_RE.match(line)
        heading_match = _HEADING_RE.match(line)
        bold_match = _BOLD_LINE_RE.match(line)

        # Compare against the un-decorated text (strip markdown bold/heading
        # markers) so "**AI/ML Engineer**" in the raw text still matches
        # the plain headline string captured separately as its own section.
        plain_text = (bold_match.group(1) if bold_match else heading_match.group(1) if heading_match else line).strip()
        if plain_text.lower() in skip_lines_lower:
            continue

        if bullet_match:
            section_type, text = "experience_bullet", bullet_match.group(1)
        elif heading_match:
            section_type, text = "experience_heading", heading_match.group(1)
        elif bold_match:
            section_type, text = "experience_heading", bold_match.group(1)
        else:
            section_type, text = "other", line

        text = _strip_markdown(text)
        if not text:
            continue

        sections.append(
            ResumeSection(section_id=f"line-{order}", section_type=section_type, text=text, order=order)
        )
        order += 1
    return sections


def build_initial_draft(job: JobListing, profile: CandidateProfile) -> TailoredDraft:
    tailored = tailor_resume_for_target(job, profile, target_score=90)
    headline_text = tailored.tailored_headline if tailored else profile.headline
    summary_text = tailored.tailored_summary if tailored else profile.summary

    sections = [
        ResumeSection(section_id="headline", section_type="headline", text=headline_text, order=0),
        ResumeSection(section_id="summary", section_type="summary", text=summary_text, order=1),
    ]
    # The candidate's name is rendered separately as the document title
    # (see agents/resume_renderer.py's render_pdf/render_docx, which take
    # candidate_name as its own arg) and the headline/summary are already
    # captured above — skip these lines when they recur in the raw text
    # split below, or the rendered draft duplicates the name/headline
    # block (confirmed via a real generated PDF during Stage 4
    # verification).
    skip_lines_lower = {profile.name.lower(), profile.headline.lower(), profile.summary.lower()}
    sections.extend(_split_into_lines(profile.resume_raw_text, skip_lines_lower))
    # Re-number after the headline/summary prefix so `order` stays a clean
    # increasing sequence for the renderer to sort by.
    for i, section in enumerate(sections):
        section.order = i

    keywords = extract_keywords(job.description or job.title)
    combined_text = " ".join(s.text for s in sections)
    found, missing = check_keyword_presence(keywords, combined_text)
    ats_score = round(100 * len(found) / len(keywords)) if keywords else 100

    return TailoredDraft(
        dedupe_key=job.dedupe_key,
        target_title=job.title,
        sections=sections,
        ats_score=ats_score,
        keywords_found=found,
        keywords_missing=missing,
    )
