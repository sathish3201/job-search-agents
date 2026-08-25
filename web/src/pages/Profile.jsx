import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt", ".md"];
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

function validateFile(file) {
  const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!ACCEPTED_EXTENSIONS.includes(ext)) {
    return `Unsupported file type '${ext || file.name}'. Supported: ${ACCEPTED_EXTENSIONS.join(", ")}.`;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return `File is too large (${(file.size / 1024 / 1024).toFixed(1)}MB) — max ${MAX_UPLOAD_BYTES / 1024 / 1024}MB.`;
  }
  return null;
}

export default function Profile() {
  const [profile, setProfile] = useState(null);
  const [error, setError] = useState("");
  const [wakingUp, setWakingUp] = useState(false);
  const [loading, setLoading] = useState(true);
  const [uploadError, setUploadError] = useState("");
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef(null);

  useEffect(() => {
    loadProfile();
  }, []);

  function loadProfile() {
    setLoading(true);
    api
      .getProfile(() => setWakingUp(true))
      .then((res) => {
        setProfile(res.profile);
        setError("");
      })
      .catch(() => setError("No profile loaded yet — upload a resume below to get started."))
      .finally(() => {
        setWakingUp(false);
        setLoading(false);
      });
  }

  async function handleFileSelected(e) {
    const file = e.target.files?.[0];
    if (!file) return;

    const validationError = validateFile(file);
    if (validationError) {
      setUploadError(validationError);
      return;
    }

    setUploadError("");
    setUploading(true);
    try {
      const res = await api.uploadResume(file);
      setProfile(res.profile);
      setError("");
    } catch (err) {
      setUploadError(err.message || "Could not upload resume.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1>Profile</h1>
      </div>

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <h3>Upload resume</h3>
        <p className="muted">
          PDF, DOCX, TXT, or MD — up to 5MB. Uploading a new resume replaces your current profile
          and is used for your next job search.
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPTED_EXTENSIONS.join(",")}
          onChange={handleFileSelected}
          disabled={uploading}
        />
        {uploading && <p className="muted">Uploading and parsing resume...</p>}
        {uploadError && <p className="error-text">{uploadError}</p>}
      </div>

      {loading && (
        <p className="muted">
          {wakingUp
            ? "Waking up the server — this can take up to a minute on the first request after it's been idle..."
            : "Loading..."}
        </p>
      )}
      {!loading && error && <p className="muted">{error}</p>}
      {!loading && profile && (
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
      )}
    </div>
  );
}
