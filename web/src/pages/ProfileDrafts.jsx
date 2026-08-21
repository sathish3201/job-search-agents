import { useEffect, useState } from "react";
import { api } from "../api/client";

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

export default function ProfileDrafts() {
  const [drafts, setDrafts] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getProfileDrafts().then((d) => {
      setDrafts(d);
      setLoading(false);
    });
  }, []);

  return (
    <div>
      <div className="page-header">
        <h1>Profile Drafts</h1>
      </div>
      <p className="muted">
        Suggested LinkedIn/Naukri headline &amp; summary updates, based on skill gaps across
        your top-matching jobs. Nothing is posted automatically — review and paste in yourself.
      </p>

      {loading ? (
        <p className="muted">Loading...</p>
      ) : drafts.length === 0 ? (
        <p className="muted">No drafts yet — run a search from the Dashboard first.</p>
      ) : (
        drafts.map((d, i) => (
          <div className="card" key={i}>
            <div className="card-header">
              <h3>{d.platform.charAt(0).toUpperCase() + d.platform.slice(1)}</h3>
            </div>

            <div className="draft-field">
              <div className="draft-label-row">
                <strong>Headline</strong>
                <CopyButton text={d.headline} />
              </div>
              <p>{d.headline}</p>
            </div>

            <div className="draft-field">
              <div className="draft-label-row">
                <strong>Summary</strong>
                <CopyButton text={d.summary} />
              </div>
              <p>{d.summary}</p>
            </div>

            <div className="muted">
              <strong>Why:</strong> {d.reasoning}
            </div>
            {d.based_on_trend && (
              <div className="muted">
                <strong>Triggered by:</strong> {d.based_on_trend}
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}
