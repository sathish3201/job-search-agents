const BASE_URL = import.meta.env.VITE_API_BASE || "http://localhost:8020";

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json();
}

export const api = {
  triggerRun: () => request("/api/pipeline/run", { method: "POST" }),
  getStatus: () => request("/api/pipeline/status"),
  getResult: () => request("/api/pipeline/result"),
  getLiveJobs: () => request("/api/pipeline/live"),

  getApplications: () => request("/api/applications"),
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

  getProfile: () => request("/api/profile"),
  getProfileDrafts: () => request("/api/profile/drafts"),

  getImprovement: () => request("/api/improvement"),
  getModelStatus: () => request("/api/model-status"),

  tailorResume: (dedupeKey) =>
    request("/api/jobs/tailor-resume", {
      method: "POST",
      body: JSON.stringify({ dedupe_key: dedupeKey }),
    }),
};
