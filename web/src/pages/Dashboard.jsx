import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

function ScoreBadge({ score }) {
  const cls = score >= 80 ? "badge badge-high" : score >= 60 ? "badge badge-mid" : "badge badge-low";
  return <span className={cls}>{score}/100</span>;
}

function JobCard({ ranked }) {
  const { job, fit_score, matching_skills, missing_skills, tailored_pitch } = ranked;
  return (
    <div className="card">
      <div className="card-header">
        <div>
          <h3>{job.title}</h3>
          <div className="muted">
            {job.company} · {job.location} {job.remote ? "· Remote" : ""}
          </div>
        </div>
        <ScoreBadge score={fit_score} />
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
      <a href={job.url} target="_blank" rel="noreferrer" className="link-btn">
        View posting →
      </a>
    </div>
  );
}

export default function Dashboard() {
  const [status, setStatus] = useState("idle");
  const [message, setMessage] = useState("");
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const pollRef = useRef(null);

  const loadResult = async () => {
    try {
      const result = await api.getResult();
      setJobs(result.ranked_jobs || []);
    } catch {
      // no result yet, ignore
    }
  };

  useEffect(() => {
    (async () => {
      await loadResult();
      setLoading(false);
    })();
    return () => clearInterval(pollRef.current);
  }, []);

  const handleRun = async () => {
    const res = await api.triggerRun();
    setStatus(res.status);
    setMessage(res.message);

    pollRef.current = setInterval(async () => {
      const s = await api.getStatus();
      setStatus(s.status);
      setMessage(s.message);
      if (s.status === "done" || s.status === "error") {
        clearInterval(pollRef.current);
        if (s.status === "done") await loadResult();
      }
    }, 3000);
  };

  const sorted = [...jobs].sort((a, b) => b.fit_score - a.fit_score);

  return (
    <div>
      <div className="page-header">
        <h1>Dashboard</h1>
        <button onClick={handleRun} disabled={status === "running"} className="primary-btn">
          {status === "running" ? "Running..." : "Run New Search"}
        </button>
      </div>
      {message && <div className={`status-banner status-${status}`}>{message}</div>}

      {loading ? (
        <p className="muted">Loading...</p>
      ) : sorted.length === 0 ? (
        <p className="muted">No jobs yet — click "Run New Search" to fetch and rank jobs.</p>
      ) : (
        <div className="card-grid">
          {sorted.map((r) => (
            <JobCard key={`${r.job.source}:${r.job.external_id}`} ranked={r} />
          ))}
        </div>
      )}
    </div>
  );
}
