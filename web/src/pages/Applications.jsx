import { useEffect, useState } from "react";
import { api } from "../api/client";

const STATUSES = ["found", "applied", "viewed_by_recruiter", "interviewing", "offer", "rejected", "ghosted"];

export default function Applications() {
  const [apps, setApps] = useState([]);
  const [loading, setLoading] = useState(true);
  const [savingKey, setSavingKey] = useState(null);

  const load = async () => {
    const data = await api.getApplications();
    setApps(data);
    setLoading(false);
  };

  useEffect(() => {
    load();
  }, []);

  const handleStatusChange = async (dedupeKey, newStatus) => {
    setSavingKey(dedupeKey);
    try {
      await api.updateApplication(dedupeKey, newStatus);
      await load();
    } finally {
      setSavingKey(null);
    }
  };

  return (
    <div>
      <div className="page-header">
        <h1>Applications</h1>
      </div>
      {loading ? (
        <p className="muted">Loading...</p>
      ) : apps.length === 0 ? (
        <p className="muted">No tracked applications yet — run a search from the Dashboard first.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Job</th>
              <th>Company</th>
              <th>Fit</th>
              <th>Status</th>
              <th>Last updated</th>
            </tr>
          </thead>
          <tbody>
            {apps.map((a) => (
              <tr key={a.dedupe_key}>
                <td>
                  <a href={a.job.url} target="_blank" rel="noreferrer">
                    {a.job.title}
                  </a>
                </td>
                <td>{a.job.company}</td>
                <td>{a.fit_score ?? "—"}</td>
                <td>
                  <select
                    value={a.status}
                    disabled={savingKey === a.dedupe_key}
                    onChange={(e) => handleStatusChange(a.dedupe_key, e.target.value)}
                  >
                    {STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s.replace(/_/g, " ")}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="muted">{a.last_updated}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
