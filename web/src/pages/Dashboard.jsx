import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

function ScoreBadge({ label, score }) {
  const cls = score >= 80 ? "badge badge-high" : score >= 60 ? "badge badge-mid" : "badge badge-low";
  return (
    <span className={cls} title={label}>
      {label} {score}/100
    </span>
  );
}

function downloadTextFile(filename, text) {
  // Browser-native download, no server round-trip needed for plain text.
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function TailoredResumePanel({ tailoring, loading, error, onClose }) {
  if (!loading && !tailoring && !error) return null;

  const handleDownload = () => {
    const text = [
      `Tailored Resume — ${tailoring.target_title}`,
      "",
      `Headline: ${tailoring.tailored_headline}`,
      "",
      `Summary: ${tailoring.tailored_summary}`,
      "",
      `ATS score: ${tailoring.original_ats_score} -> ${tailoring.final_ats_score}`,
      tailoring.keywords_added.length
        ? `Keywords added: ${tailoring.keywords_added.join(", ")}`
        : "",
      "",
      tailoring.reasoning,
    ]
      .filter(Boolean)
      .join("\n");
    downloadTextFile(
      `tailored-resume-${tailoring.target_title.replace(/\s+/g, "-").toLowerCase()}.txt`,
      text
    );
  };

  return (
    <div className="side-panel">
      <div className="side-panel-header">
        <h2>Tailored Resume</h2>
        <button onClick={onClose} className="icon-btn" aria-label="Close">
          ×
        </button>
      </div>
      {loading && <p className="muted">Tailoring resume…</p>}
      {error && <p className="error-text">{error}</p>}
      {tailoring && (
        <>
          <p className="muted">For: {tailoring.target_title}</p>
          <div className="ats-score-row">
            <span>ATS score: </span>
            <strong>
              {tailoring.original_ats_score} → {tailoring.final_ats_score}
            </strong>
          </div>

          <h4>Headline</h4>
          <p>{tailoring.tailored_headline}</p>

          <h4>Summary</h4>
          <p>{tailoring.tailored_summary}</p>

          {tailoring.keywords_added.length > 0 && (
            <>
              <h4>Keywords added</h4>
              <p className="muted">{tailoring.keywords_added.join(", ")}</p>
            </>
          )}

          <h4>Why</h4>
          <p className="muted">{tailoring.reasoning}</p>

          <button onClick={handleDownload} className="primary-btn" style={{ marginTop: "1rem" }}>
            Download
          </button>
        </>
      )}
      {!loading && !error && !tailoring && (
        <p className="muted">
          This resume already covers enough ATS keywords for this job — no truthful improvement
          found.
        </p>
      )}
    </div>
  );
}

function JobCard({ ranked, onTailor, tailoringKey, onApply, onDiscard, applyState }) {
  const { job, fit_score, ats_score, matching_skills, missing_skills, tailored_pitch } = ranked;
  const dedupeKey = `${job.source}:${job.external_id}`;
  const isTailoring = tailoringKey === dedupeKey;
  const state = applyState[dedupeKey] || {};

  if (state.discarded) return null; // discarded jobs drop out of the list immediately

  return (
    <div className="card">
      <div className="card-header">
        <div>
          <h3>{job.title}</h3>
          <div className="muted">
            {job.company} · {job.location} {job.remote ? "· Remote" : ""}
          </div>
        </div>
        <div className="badge-stack">
          <ScoreBadge label="Fit" score={fit_score} />
          <ScoreBadge label="ATS" score={ats_score} />
        </div>
      </div>
      <div className="skill-row">
        <div>
          <strong>Matches:</strong>{" "}
          {matching_skills.length ? matching_skills.join(", ") : "—"}
        </div>
        <div>
          <strong>Gaps:</strong>{" "}
          {missing_skills.length ? missing_skills.join(", ") : "—"}
        </div>
      </div>
      {tailored_pitch && <p className="pitch">{tailored_pitch}</p>}
      {state.result && (
        <p className={state.result.success ? "apply-success" : "apply-note"}>
          {state.result.message}
        </p>
      )}
      <div className="card-actions">
        <a href={job.url} target="_blank" rel="noreferrer" className="link-btn">
          View posting →
        </a>
        <button onClick={() => onTailor(dedupeKey)} disabled={isTailoring} className="secondary-btn">
          {isTailoring ? "Tailoring…" : "Tailor Resume"}
        </button>
        {!state.result?.success && (
          <>
            <button
              onClick={() => onApply(dedupeKey, job)}
              disabled={state.applying}
              className="primary-btn"
              title="Opens a real browser on the machine running this dashboard's backend — only works when that's your local machine"
            >
              {state.applying ? "Opening browser…" : "Apply"}
            </button>
            <button onClick={() => onDiscard(dedupeKey)} className="secondary-btn">
              Discard
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function ApplyConfirmModal({ job, onConfirm, onCancel, submitting }) {
  const [phrase, setPhrase] = useState("");
  const REQUIRED = "I ACCEPT THE BAN RISK";

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Apply to {job.title} at {job.company}?</h3>
        <p className="muted">
          This opens a real browser on the machine running the backend and submits (or opens for
          manual completion) a real application under your account. LinkedIn/most job boards
          prohibit automated applications — accounts used for this can be flagged or banned. This
          only works if the dashboard is pointed at your own locally-running backend, not the
          deployed one (no headless server can solve a CAPTCHA for you).
        </p>
        <p>
          Type <code>{REQUIRED}</code> to confirm:
        </p>
        <input
          type="text"
          value={phrase}
          onChange={(e) => setPhrase(e.target.value)}
          className="confirm-input"
          autoFocus
        />
        <div className="modal-actions">
          <button onClick={onCancel} className="secondary-btn" disabled={submitting}>
            Cancel
          </button>
          <button
            onClick={() => onConfirm(phrase)}
            disabled={phrase !== REQUIRED || submitting}
            className="primary-btn"
          >
            {submitting ? "Applying…" : "Confirm & Apply"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [status, setStatus] = useState("idle");
  const [message, setMessage] = useState("");
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const pollRef = useRef(null);
  const livePollRef = useRef(null);
  // Default on: jobs appear incrementally as the pipeline qualifies them,
  // instead of one reveal when the whole run finishes. User can switch back
  // to the old batch behavior — SOLID: this component doesn't hard-code one
  // display strategy, it picks between two independently.
  const [streamingEnabled, setStreamingEnabled] = useState(true);

  const [tailoringKey, setTailoringKey] = useState(null);
  const [tailoring, setTailoring] = useState(null);
  const [tailorError, setTailorError] = useState("");
  // Separate from `tailoring` on purpose: the API can legitimately return
  // null (no truthful ATS improvement found), and null is also `tailoring`'s
  // untouched initial value — without this flag, panelOpen couldn't tell
  // "haven't tailored anything yet" apart from "tailored, backend said null",
  // and the panel would silently never open for that valid response.
  const [hasTailored, setHasTailored] = useState(false);

  // Keyed by dedupeKey: { applying: bool, result: {success, message} | null,
  // discarded: bool }. Kept separate from the job objects themselves since
  // apply/discard state is UI-session state, not part of what the pipeline
  // returns.
  const [applyState, setApplyState] = useState({});
  const [pendingApply, setPendingApply] = useState(null); // {dedupeKey, job} | null — drives the confirm modal

  const loadResult = async () => {
    try {
      const result = await api.getResult();
      // ats_passed_jobs, not ranked_jobs: only jobs that cleared both the
      // LLM fit threshold and the ATS keyword-match threshold (>=75) are
      // meant to be displayed here.
      setJobs(result.ats_passed_jobs || []);
    } catch {
      // no result yet, ignore
    }
  };

  useEffect(() => {
    (async () => {
      await loadResult();
      setLoading(false);
    })();
    return () => {
      clearInterval(pollRef.current);
      clearInterval(livePollRef.current);
    };
  }, []);

  const handleRun = async () => {
    const res = await api.triggerRun();
    setStatus(res.status);
    setMessage(res.message);
    if (streamingEnabled) setJobs([]); // start the incremental reveal from empty

    pollRef.current = setInterval(async () => {
      const s = await api.getStatus();
      setStatus(s.status);
      setMessage(s.message);
      if (s.status === "done" || s.status === "error") {
        clearInterval(pollRef.current);
        clearInterval(livePollRef.current);
        if (s.status === "done") await loadResult(); // final authoritative list replaces the streamed one
      }
    }, 3000);

    if (streamingEnabled) {
      // Same 3s cadence as the status poll — one extra lightweight GET per
      // tick, not a separate faster loop, so a run in progress doesn't add
      // meaningfully more request volume than before streaming existed.
      livePollRef.current = setInterval(async () => {
        try {
          const live = await api.getLiveJobs();
          setJobs(live || []);
        } catch {
          // transient — next tick retries
        }
      }, 3000);
    }
  };

  const handleTailor = async (dedupeKey) => {
    setTailoringKey(dedupeKey);
    setTailoring(null);
    setTailorError("");
    setHasTailored(false);
    try {
      const result = await api.tailorResume(dedupeKey);
      setTailoring(result); // null is a valid response — "no truthful improvement"
      setHasTailored(true);
    } catch (err) {
      setTailorError(err.message || "Could not tailor resume for this job.");
    } finally {
      setTailoringKey(null);
    }
  };

  const handleApplyClick = (dedupeKey, job) => {
    setPendingApply({ dedupeKey, job });
  };

  const handleApplyConfirm = async (confirmationPhrase) => {
    const { dedupeKey } = pendingApply;
    setApplyState((prev) => ({ ...prev, [dedupeKey]: { ...prev[dedupeKey], applying: true } }));
    try {
      const result = await api.applyToJob(dedupeKey, confirmationPhrase);
      setApplyState((prev) => ({ ...prev, [dedupeKey]: { applying: false, result } }));
    } catch (err) {
      setApplyState((prev) => ({
        ...prev,
        [dedupeKey]: {
          applying: false,
          result: { success: false, message: err.message || "Apply request failed." },
        },
      }));
    } finally {
      setPendingApply(null);
    }
  };

  const handleDiscard = async (dedupeKey) => {
    setApplyState((prev) => ({ ...prev, [dedupeKey]: { ...prev[dedupeKey], discarded: true } }));
    try {
      await api.discardApplication(dedupeKey);
    } catch {
      // Already reflected optimistically in the UI; a failed PATCH here
      // just means the tracker file wasn't updated — not worth reverting
      // the UI over, the job is still gone from view either way.
    }
  };

  const sorted = [...jobs].sort((a, b) => b.fit_score - a.fit_score);
  const panelOpen = tailoringKey !== null || hasTailored || tailorError !== "";

  return (
    <div className={panelOpen ? "layout-with-panel" : ""}>
      <div className="main-column">
        <div className="page-header">
          <h1>Dashboard</h1>
          <div className="page-header-actions">
            <label className="stream-toggle" title="Show jobs as they qualify, or wait for the full run">
              <input
                type="checkbox"
                checked={streamingEnabled}
                onChange={(e) => setStreamingEnabled(e.target.checked)}
                disabled={status === "running"}
              />
              Live results
            </label>
            <button onClick={handleRun} disabled={status === "running"} className="primary-btn">
              {status === "running" ? "Running..." : "Run New Search"}
            </button>
          </div>
        </div>
        {message && <div className={`status-banner status-${status}`}>{message}</div>}

        {loading ? (
          <p className="muted">Loading...</p>
        ) : sorted.length === 0 ? (
          <p className="muted">
            No jobs yet — click "Run New Search" to fetch, rank, and ATS-check jobs. Only jobs
            scoring 75+ on the ATS keyword check are shown here.
          </p>
        ) : (
          <div className="card-grid">
            {sorted.map((r) => (
              <JobCard
                key={`${r.job.source}:${r.job.external_id}`}
                ranked={r}
                onTailor={handleTailor}
                tailoringKey={tailoringKey}
                onApply={handleApplyClick}
                onDiscard={handleDiscard}
                applyState={applyState}
              />
            ))}
          </div>
        )}
      </div>

      {panelOpen && (
        <TailoredResumePanel
          tailoring={tailoring}
          loading={tailoringKey !== null}
          error={tailorError}
          onClose={() => {
            setTailoring(null);
            setTailorError("");
            setHasTailored(false);
          }}
        />
      )}

      {pendingApply && (
        <ApplyConfirmModal
          job={pendingApply.job}
          submitting={applyState[pendingApply.dedupeKey]?.applying || false}
          onConfirm={handleApplyConfirm}
          onCancel={() => setPendingApply(null)}
        />
      )}
    </div>
  );
}
