import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import TailorDialog from "../components/TailorDialog";

function ScoreBadge({ label, score }) {
  const cls = score >= 80 ? "badge badge-high" : score >= 60 ? "badge badge-mid" : "badge badge-low";
  return (
    <span className={cls} title={label}>
      {label} {score}/100
    </span>
  );
}

// TailoredResumePanel (headline/summary-only, plain-text preview) was
// replaced by TailorDialog (web/src/components/TailorDialog.jsx) — a
// full two-pane, chat-driven, PDF/DOCX-exporting editor covering any
// resume section, not just headline/summary. See the interactive resume
// tailor agent plan for the full rationale.

// Mirrors api/routers/tailor.py's TAILOR_ATS_MIN — kept in sync manually
// since this is a small UI-only display gate, not worth a round-trip
// just to fetch a constant. Below this, tailoring would need fabricated
// skills to close the gap (the backend's fabrication guard would strip
// them anyway), so the button doesn't appear rather than offering a
// mostly-wasted action.
const TAILOR_ATS_MIN = 70;

function JobCard({ ranked, onTailor, onApply, onDiscard, applyState }) {
  const { job, fit_score, ats_score, matching_skills, missing_skills, tailored_pitch } = ranked;
  const dedupeKey = `${job.source}:${job.external_id}`;
  const state = applyState[dedupeKey] || {};
  const canTailor = ats_score >= TAILOR_ATS_MIN;

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
        {canTailor ? (
          <button onClick={() => onTailor(dedupeKey)} className="secondary-btn">
            Tailor Resume
          </button>
        ) : (
          <span
            className="muted"
            title={`ATS score ${ats_score} is below ${TAILOR_ATS_MIN} — tailoring is only offered for jobs already reasonably close`}
          >
            Tailoring unavailable (ATS &lt; {TAILOR_ATS_MIN})
          </span>
        )}
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

  // Minimum ATS score (dashboard dropdown, 50-100) a ranked job must clear
  // to get added to the Applications tracker for this run — see
  // agents/graph.py's min_ats_score field, which this feeds.
  const [minAtsScore, setMinAtsScore] = useState(50);

  // dedupeKey of the job currently open in TailorDialog, or null when
  // closed — the dialog owns all of its own session/chat/draft state
  // internally (see web/src/components/TailorDialog.jsx), so this is the
  // only piece Dashboard needs to track.
  const [tailorDialogKey, setTailorDialogKey] = useState(null);

  // Keyed by dedupeKey: { applying: bool, result: {success, message} | null,
  // discarded: bool }. Kept separate from the job objects themselves since
  // apply/discard state is UI-session state, not part of what the pipeline
  // returns.
  const [applyState, setApplyState] = useState({});
  const [pendingApply, setPendingApply] = useState(null); // {dedupeKey, job} | null — drives the confirm modal

  // True only while the very first load's request is taking cold-start-
  // length time — see api/client.js's COLD_START_THRESHOLD_MS. Not used
  // for the post-run reload (the server is already warm by definition at
  // that point, so it's never passed onSlow there).
  const [wakingUp, setWakingUp] = useState(false);

  const loadResult = async (isInitialLoad = false) => {
    try {
      const result = await api.getResult(isInitialLoad ? () => setWakingUp(true) : undefined);
      // ranked_jobs (the full superset), not ats_passed_jobs: the user
      // wants to see the actual ATS score range across every fit-passed
      // job, not just a binary shown/hidden cutoff. Tailor Resume is
      // separately gated at ats_score >= TAILOR_ATS_MIN below — a low
      // score still shows on the dashboard, it just doesn't offer
      // tailoring (which would need fabricated skills to close that big a
      // gap, and the fabrication guard would just strip them anyway).
      setJobs(result.ranked_jobs || []);
    } catch {
      // no result yet, ignore
    } finally {
      setWakingUp(false);
    }
  };

  useEffect(() => {
    (async () => {
      await loadResult(true);
      setLoading(false);
    })();
    return () => {
      clearInterval(pollRef.current);
      clearInterval(livePollRef.current);
    };
  }, []);

  const handleRun = async () => {
    const res = await api.triggerRun(minAtsScore);
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

  const handleTailor = (dedupeKey) => setTailorDialogKey(dedupeKey);

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

  return (
    <div>
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
            <label
              className="stream-toggle"
              title="Only jobs with an ATS score at or above this get added to your Applications tracker"
            >
              Min ATS to track
              <select
                value={minAtsScore}
                onChange={(e) => setMinAtsScore(Number(e.target.value))}
                disabled={status === "running"}
                style={{ marginLeft: "0.4rem" }}
              >
                {[50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100].map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <button onClick={handleRun} disabled={status === "running"} className="primary-btn">
              {status === "running" ? "Running..." : "Run New Search"}
            </button>
          </div>
        </div>
        {message && <div className={`status-banner status-${status}`}>{message}</div>}

        {loading ? (
          <p className="muted">
            {wakingUp
              ? "Waking up the server — this can take up to a minute on the first request after it's been idle..."
              : "Loading..."}
          </p>
        ) : sorted.length === 0 ? (
          <p className="muted">
            No jobs yet — click "Run New Search" to fetch, rank, and ATS-check jobs. Every job
            that clears the fit-score check is shown here, with its actual ATS score — Tailor
            Resume is offered once ATS score is 70+.
          </p>
        ) : (
          <div className="card-grid">
            {sorted.map((r) => (
              <JobCard
                key={`${r.job.source}:${r.job.external_id}`}
                ranked={r}
                onTailor={handleTailor}
                onApply={handleApplyClick}
                onDiscard={handleDiscard}
                applyState={applyState}
              />
            ))}
          </div>
        )}
      </div>

      {tailorDialogKey && (
        <TailorDialog dedupeKey={tailorDialogKey} onClose={() => setTailorDialogKey(null)} />
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
