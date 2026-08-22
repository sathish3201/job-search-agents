import { useEffect, useState } from "react";
import { api } from "../api/client";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8020";
const POLL_INTERVAL_MS = 60_000;

// Same pattern as the portfolio site's own StatusBadge: the API dot is a
// plain fetch to this project's own /health. The Model dot goes through
// this API's /model-status route rather than hitting the LLM tunnel
// directly from the browser — confirmed by a real CORS preflight test
// that the Termux llama-server behind the tunnel sends no
// Access-Control-Allow-Origin header, so a direct cross-origin fetch from
// the deployed frontend would be blocked by the browser even though the
// tunnel itself is reachable. A server-to-server check from this API
// isn't subject to that restriction.
function usePolledOnline(check) {
  const [online, setOnline] = useState(null);

  useEffect(() => {
    let cancelled = false;

    function run() {
      check()
        .then((result) => {
          if (!cancelled) setOnline(result);
        })
        .catch(() => {
          if (!cancelled) setOnline(false);
        });
    }

    run();
    const interval = setInterval(run, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [check]);

  return online;
}

function Dot({ status }) {
  const cls = status === true ? "status-dot-online" : status === false ? "status-dot-offline" : "status-dot-unknown";
  return <span className={`status-dot ${cls}`} />;
}

export default function StatusBadge() {
  const apiOnline = usePolledOnline(() =>
    fetch(`${API_BASE}/health`, { cache: "no-store" }).then((res) => res.ok)
  );
  const modelOnline = usePolledOnline(() => api.getModelStatus().then((json) => json.online === true));

  return (
    <div className="status-badge" title="Live connection status">
      <span className="status-item">
        <Dot status={apiOnline} />
        API
      </span>
      <span className="status-sep" />
      <span className="status-item">
        <Dot status={modelOnline} />
        Model
      </span>
    </div>
  );
}
