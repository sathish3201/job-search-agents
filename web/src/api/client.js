const BASE_URL = import.meta.env.VITE_API_BASE || "http://localhost:8020";

// Render's free tier spins the API down after ~15 min idle; the first
// request after that has to wait for a real cold boot (observed
// 30-50s), not just normal network latency. A generic "Loading..."
// spinner reads as broken at that point, so callers making an
// on-mount fetch can pass onSlow to get a distinct "waking up the
// server" message once this threshold passes without a response —
// short enough that it doesn't fire on a merely-slow LLM call in the
// pipeline-status polling path (those callers don't pass onSlow).
const COLD_START_THRESHOLD_MS = 6000;

async function request(path, options = {}) {
  const { onSlow, ...fetchOptions } = options;
  let slowTimer;
  if (onSlow) {
    slowTimer = setTimeout(onSlow, COLD_START_THRESHOLD_MS);
  }
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...fetchOptions,
    });
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`${res.status} ${res.statusText}: ${body}`);
    }
    return await res.json();
  } finally {
    clearTimeout(slowTimer);
  }
}

export const api = {
  triggerRun: () => request("/api/pipeline/run", { method: "POST" }),
  getStatus: () => request("/api/pipeline/status"),
  // onSlow: called once if the request is still pending past
  // COLD_START_THRESHOLD_MS — pass it on a page's initial on-mount
  // fetch so the UI can distinguish "Render is cold-starting" from a
  // normal brief loading flicker. Omit it (as getStatus/getLiveJobs
  // do, used only for in-flight-run polling) where a slow response is
  // already expected and shouldn't trigger the cold-start message.
  getResult: (onSlow) => request("/api/pipeline/result", { onSlow }),
  getLiveJobs: () => request("/api/pipeline/live"),

  getApplications: (onSlow) => request("/api/applications", { onSlow }),
  updateApplication: (dedupeKey, status, note = "") =>
    request(`/api/applications/${encodeURIComponent(dedupeKey)}`, {
      method: "PATCH",
      body: JSON.stringify({ status, note }),
    }),
  // HIGH RISK — see agents/apply_playwright.py. Only works when this
  // dashboard is pointed at a locally-running API (not the deployed
  // Render one): it opens a real headed browser on whatever machine runs
  // the backend, and a human needs to be there for CAPTCHAs/2FA and the
  // final submit confirmation.
  applyToJob: (dedupeKey, confirmationPhrase) =>
    request(`/api/applications/${encodeURIComponent(dedupeKey)}/apply`, {
      method: "POST",
      body: JSON.stringify({ confirmation_phrase: confirmationPhrase }),
    }),
  discardApplication: (dedupeKey) =>
    request(`/api/applications/${encodeURIComponent(dedupeKey)}/discard`, {
      method: "POST",
    }),

  getProfile: (onSlow) => request("/api/profile", { onSlow }),
  getProfileDrafts: () => request("/api/profile/drafts"),

  getImprovement: () => request("/api/improvement"),
  getModelStatus: () => request("/api/model-status"),

  tailorResume: (dedupeKey) =>
    request("/api/jobs/tailor-resume", {
      method: "POST",
      body: JSON.stringify({ dedupe_key: dedupeKey }),
    }),
};
