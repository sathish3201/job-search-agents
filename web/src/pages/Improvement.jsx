import { useEffect, useState } from "react";
import { api } from "../api/client";

export default function Improvement() {
  const [report, setReport] = useState(null);

  useEffect(() => {
    api.getImprovement().then(setReport);
  }, []);

  if (!report) return <p className="muted">Loading...</p>;

  return (
    <div>
      <div className="page-header">
        <h1>Improvement</h1>
      </div>
      <div className="card">
        <p>{report.summary}</p>
        <div className="stat-row">
          <div>
            <strong>{report.average_fit_score}</strong>
            <div className="muted">average fit score</div>
          </div>
        </div>
      </div>

      <h3>What to improve next</h3>
      {report.top_missing_skills.length === 0 ? (
        <p className="muted">No consistent gaps found.</p>
      ) : (
        <div className="card-grid">
          {report.top_missing_skills.map((gap) => (
            <div className="card" key={gap.skill}>
              <div className="card-header">
                <h3>{gap.skill}</h3>
                <span className="badge badge-low">missing from {gap.frequency}</span>
              </div>
              <div className="muted">
                Seen in: {gap.sample_jobs.join(", ")}
              </div>
            </div>
          ))}
        </div>
      )}

      <h3>Your strongest matching skills</h3>
      <div className="chip-row">
        {report.strongest_matching_skills.map((s) => (
          <span key={s} className="chip chip-accent">
            {s}
          </span>
        ))}
      </div>
    </div>
  );
}
