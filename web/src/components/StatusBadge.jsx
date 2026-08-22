import { useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8020";
const MODEL_URL = import.meta.env.VITE_MODEL_URL || "";
const POLL_INTERVAL_MS = 60_000;

// Checked entirely from the browser, no backend route involved: the API
// dot hits this project's own /health, the Model dot hits the LLM
// tunnel's /health directly (ollama-service's cheap health check, not a
// real chat completion — no reason to spend an inference call just to
// render a status dot). Same pattern as the portfolio site's StatusBadge,
// adapted to this project's two actual backends instead of one shared
// context.
function usePolledOnline(url, { enabled = true } = {}) {
  const [online, setOnline] = useState(null);

  useEffect(() => {
    if (!enabled) {
      setOnline(null);
      return;
    }
    let cancelled = false;

    function check() {
      fetch(url, { cache: "no-store" })
        .then((res) => {
          if (!cancelled) setOnline(res.ok);
        })
        .catch(() => {
          if (!cancelled) setOnline(false);
        });
    }

    check();
    const interval = setInterval(check, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [url, enabled]);

  return online;
}

function Dot({ status }) {
  const cls = status === true ? "status-dot-online" : status === false ? "status-dot-offline" : "status-dot-unknown";
  return <span className={`status-dot ${cls}`} />;
}

export default function StatusBadge() {
  const apiOnline = usePolledOnline(`${API_BASE}/health`);
  const modelOnline = usePolledOnline(`${MODEL_URL}/health`, { enabled: Boolean(MODEL_URL) });

  return (
    <div className="status-badge" title="Live connection status">
      <span className="status-item">
        <Dot status={apiOnline} />
        API
      </span>
      <span className="status-sep" />
      <span className="status-item">
        <Dot status={MODEL_URL ? modelOnline : null} />
        Model
      </span>
    </div>
  );
}
