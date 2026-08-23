import { useEffect, useState } from "react";
import { api } from "../api/client";

export default function Profile() {
  const [profile, setProfile] = useState(null);
  const [error, setError] = useState("");
  const [wakingUp, setWakingUp] = useState(false);

  useEffect(() => {
    api
      .getProfile(() => setWakingUp(true))
      .then((res) => setProfile(res.profile))
      .catch(() => setError("No profile loaded yet — run a search from the Dashboard first."))
      .finally(() => setWakingUp(false));
  }, []);

  if (error) return <p className="muted">{error}</p>;
  if (!profile) {
    return (
      <p className="muted">
        {wakingUp
          ? "Waking up the server — this can take up to a minute on the first request after it's been idle..."
          : "Loading..."}
      </p>
    );
  }

  return (
    <div>
      <div className="page-header">
        <h1>Profile</h1>
      </div>
      <div className="card">
        <h2>{profile.name}</h2>
        <p className="muted">{profile.headline}</p>
        <p>{profile.summary}</p>
        <div className="stat-row">
          <div>
            <strong>{profile.years_experience}</strong>
            <div className="muted">years experience</div>
          </div>
          <div>
            <strong>{profile.skills.length}</strong>
            <div className="muted">skills identified</div>
          </div>
        </div>
        <h3>Skills</h3>
        <div className="chip-row">
          {profile.skills.map((s) => (
            <span key={s} className="chip">
              {s}
            </span>
          ))}
        </div>
        <h3>Target roles</h3>
        <div className="chip-row">
          {profile.target_roles.map((r) => (
            <span key={r} className="chip chip-accent">
              {r}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
