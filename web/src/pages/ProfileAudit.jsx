import { useState } from "react";

// Static audit content, generated once from resume + portfolio data (see the
// published artifact this was originally built as). Not fetched from the
// backend or from live LinkedIn/Naukri — refresh this by hand when the
// resume changes meaningfully, rather than re-scraping either platform on a
// schedule, which would carry the same automated-access risk as
// agents/automation.py already warns about for write actions, just for
// reads instead. This page is read-only coaching content: nothing here
// posts anywhere automatically.
const HEADLINES = [
  {
    label: "Keyword-maximum",
    note: "best for search volume",
    text: "AI/ML Engineer · Agentic AI & RAG Systems Builder | LangGraph, MCP, LLM Fine-Tuning | Python, FastAPI, Oracle PL/SQL",
  },
  {
    label: "Hook-first",
    note: "best for human click-through",
    text: "I build AI agents that actually ship — RAG, MCP tool servers, multi-agent orchestration — with production discipline from 2 years at Accenture",
  },
  {
    label: "Role-target",
    note: "best if actively job-hunting a specific title",
    text: "Generative AI Engineer | RAG · LangGraph · MCP · LLM Fine-Tuning | Open to AI/ML Engineer roles",
  },
];

const ABOUT_PARAGRAPHS = [
  "I build AI systems that don't just demo well — they run in production, validate their own outputs, and hold up under real load.",
  "Over the past two years at Accenture, I've lived on both sides of that line: writing Oracle PL/SQL that reduced recurring defects by 25% and cut query execution time by 35% in a live data warehouse — and, on my own time, building agentic AI systems with the same discipline. My flagship project, Local Agent Pipeline, turns natural language into validated SQL (checked by a read-only SqlGuard before it ever executes), retrieves answers via RAG over 5,500+ transcript chunks in ChromaDB, and exposes every capability as a callable tool through an MCP server — all running locally, containerized with Docker, deployed on Render.",
  "That's the pattern across everything I build: retrieval and generation are the easy half. Validation, caching, and clean interfaces are what make an agentic system trustworthy enough to actually use. I've applied it to a multi-agent job-search pipeline (LangGraph) with a truthfulness-guarded resume tailoring agent, a MERN-stack interview platform with live LLM grading, and an n8n workflow that triages ETL incidents automatically.",
  "I'm most energized by systems where an LLM has to prove its output is correct, not just plausible — and I bring the same SLA-driven rigor from enterprise data work into every one of them.",
];

const SKILLS = [
  "RAG (Retrieval-Augmented Generation)",
  "LangGraph",
  "Model Context Protocol (MCP)",
  "Python",
  "LLM Fine-Tuning",
  "Prompt Engineering",
  "FastAPI",
  "Vector Databases (ChromaDB)",
  "Multi-Agent Systems",
  "Oracle PL/SQL",
  "SQL Performance Tuning",
  "Node.js",
  "React",
  "Docker",
  "REST API Design",
];

const KEYWORDS = [
  "Generative AI",
  "LLM Orchestration",
  "Agentic AI",
  "Machine Learning",
  "Tool-Calling",
  "Vector Search",
  "NL-to-SQL",
  "API Integration",
  "Data Engineering",
  "Backend Engineer",
  "Production Deployment",
];

// LinkedIn and Naukri optimize for different signals: LinkedIn is
// keyword/headline/network-driven search, Naukri is more keyword-in-resume
// and "resume headline" field driven with less emphasis on a long-form
// About section. Kept as two explicit variants rather than one generic
// list, since pasting the LinkedIn About paragraph structure into Naukri's
// much shorter profile summary field would just get truncated.
const PLATFORM_NOTES = {
  linkedin: {
    label: "LinkedIn",
    headlineField: "Headline (220 char limit)",
    summaryField: "About section",
    summaryGuidance:
      "Use the full multi-paragraph About rewrite below — LinkedIn shows ~3 lines before \"see more\", so the opening two sentences have to work standalone.",
    extra:
      "Also update: Featured section (pin your top 3 live-deployed projects), Skills (reorder so RAG/LangGraph/MCP are the top 3 — Profile → Skills → Reorder), and add your 4 certifications as individual Licenses & Certifications entries.",
  },
  naukri: {
    label: "Naukri",
    headlineField: "Resume headline (single line, ~250 char limit)",
    summaryField: "Profile summary",
    summaryGuidance:
      "Naukri's profile summary is shorter and more keyword-dense than LinkedIn's About — use a condensed 2-3 sentence version rather than the full paragraph set, and keep the resume headline itself close to the Keyword-maximum option below since Naukri's search is more literal keyword matching (same mechanism agents/ats_checker.py's deterministic ATS check already mirrors).",
    extra:
      "Also update: Key Skills section (same top-15 list below applies directly — Naukri surfaces these prominently in recruiter search filters), and re-upload your resume PDF so its text matches — Naukri's search also indexes the attached resume file, not just profile fields.",
  },
};

// Verified against a real LinkedIn "Save to PDF" export (2026-08-23) — the
// only reliable way to check live profile content without an automated,
// authenticated scrape (see agents/apply_playwright.py's module docstring
// for why that's a real ban-risk action, not a technicality). Update this
// list by hand each time a fresh export is checked; it deliberately does
// not try to infer status automatically.
const LINKEDIN_PROGRESS = [
  {
    status: "done",
    title: "Headline",
    detail: "Live profile matches the Role-target rewrite exactly.",
  },
  {
    status: "done",
    title: "About section",
    detail: "Full rewrite is live, including the closing CTA line.",
  },
  {
    status: "done",
    title: "Experience bullets",
    detail: "All 5 outcome-verb, metric-led bullets are live.",
  },
  {
    status: "done",
    title: "Top 3 pinned skills",
    detail: "MCP, LangGraph, RAG are pinned first, as recommended.",
  },
  {
    status: "missing",
    title: "Certifications section has the wrong entries",
    detail:
      "Currently shows generic filler (\"Viewing & printing worksheets in Excel 365\", \"Using Punctuation Marks\", \"Year 1 Phase 1: 0 to 3 months\") instead of your real ones. Delete the filler, add: Java Full Stack Certification (Wipro TalentNext), AI/ML Virtual Internship Certificate, Smart Coder Certification (Global Rank 1402/22591, Smart Interviews), HTML Attributes & Tags (GL Academy).",
  },
  {
    status: "unknown",
    title: "Featured section",
    detail:
      "Not visible in LinkedIn's PDF export format, so status can't be confirmed from a PDF check alone — verify directly on your profile whether your top 3 projects (Local Agent Pipeline, Job Search Agent, MockGenius AI) are pinned with live demo links.",
  },
  {
    status: "unknown",
    title: "Recommendations",
    detail: "Not visible in the PDF export either — check directly on your profile page.",
  },
  {
    status: "weak",
    title: "\"Blog\" contact link",
    detail:
      "Points to linkedin.com/safety/go/ — LinkedIn's generic external-link warning redirect, not a real destination. Looks broken to anyone who clicks it; replace with GitHub or your portfolio site, or remove it.",
  },
];

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <button className="link-btn" onClick={handleCopy}>
      {copied ? "Copied!" : "Copy"}
    </button>
  );
}

export default function ProfileAudit() {
  const [platform, setPlatform] = useState("linkedin");
  const notes = PLATFORM_NOTES[platform];

  return (
    <div>
      <div className="page-header">
        <h1>Profile Audit</h1>
        <div className="page-header-actions">
          <label className="stream-toggle">
            <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
              <option value="linkedin">LinkedIn</option>
              <option value="naukri">Naukri</option>
            </select>
          </label>
        </div>
      </div>
      <p className="muted">
        Rewritten headline, About/summary, and skills for {notes.label} — built from your resume
        and portfolio content. Nothing here is posted automatically; copy each section into{" "}
        {notes.label}'s own editor yourself. Refresh this page's content by hand when your resume
        changes meaningfully — it isn't re-generated from live {notes.label} data.
      </p>

      {platform === "linkedin" && (
        <div className="card">
          <h3>Live progress (checked against a real profile export)</h3>
          <p className="muted">
            Verified against a LinkedIn "Save to PDF" export on 2026-08-23 — the safe way to check
            live content without an automated, authenticated scrape.
          </p>
          {LINKEDIN_PROGRESS.map((item) => (
            <div className="check-row-inline" key={item.title}>
              <span className={`status-dot status-${item.status}`} />
              <div>
                <strong>{item.title}</strong>
                <p className="muted" style={{ margin: "0.15rem 0 0" }}>
                  {item.detail}
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="card">
        <h3>{notes.headlineField}</h3>
        {HEADLINES.map((h) => (
          <div className="draft-field" key={h.label}>
            <div className="draft-label-row">
              <strong>
                {h.label} <span className="muted">— {h.note}</span>
              </strong>
              <CopyButton text={h.text} />
            </div>
            <p>{h.text}</p>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>{notes.summaryField}</h3>
        <p className="muted">{notes.summaryGuidance}</p>
        <div className="draft-field">
          <div className="draft-label-row">
            <strong>Full rewrite</strong>
            <CopyButton text={ABOUT_PARAGRAPHS.join("\n\n")} />
          </div>
          {ABOUT_PARAGRAPHS.map((p, i) => (
            <p key={i}>{p}</p>
          ))}
        </div>
      </div>

      <div className="card">
        <h3>Top skills to list (ranked)</h3>
        <p className="muted">{notes.extra}</p>
        <div className="chip-row">
          {SKILLS.map((s, i) => (
            <span key={s} className={i < 3 ? "chip chip-accent" : "chip"}>
              {s}
            </span>
          ))}
        </div>
      </div>

      <div className="card">
        <h3>Keywords currently missing from your profile</h3>
        <p className="muted">
          Present in your actual work but underused in your current profile text — add these into
          your headline/summary/skills rather than leaving them implicit.
        </p>
        <div className="chip-row">
          {KEYWORDS.map((k) => (
            <span key={k} className="chip">
              {k}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
