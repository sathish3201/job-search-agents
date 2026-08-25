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

const TOKEN_KEY = "job_search_agent_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

// Set by AuthContext on mount — lets request() redirect to /login on a 401
// (expired/invalid token) without this module needing to know about React
// Router. A plain callback avoids importing react-router-dom into a
// non-component file just for navigation.
let onUnauthorized = null;
export function setUnauthorizedHandler(handler) {
  onUnauthorized = handler;
}

async function request(path, options = {}) {
  const { onSlow, skipAuthRedirect, ...fetchOptions } = options;
  let slowTimer;
  if (onSlow) {
    slowTimer = setTimeout(onSlow, COLD_START_THRESHOLD_MS);
  }
  const headers = { "Content-Type": "application/json", ...fetchOptions.headers };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      ...fetchOptions,
      headers,
    });
    if (res.status === 401 && !skipAuthRedirect) {
      clearToken();
      if (onUnauthorized) onUnauthorized();
      throw new Error("401 Unauthorized");
    }
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`${res.status} ${res.statusText}: ${body}`);
    }
    return await res.json();
  } finally {
    clearTimeout(slowTimer);
  }
}

// multipart/form-data upload needs its own path — no Content-Type header
// (the browser sets the multipart boundary), otherwise same auth/401
// handling as request().
async function uploadRequest(path, formData) {
  const headers = {};
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers,
    body: formData,
  });
  if (res.status === 401) {
    clearToken();
    if (onUnauthorized) onUnauthorized();
    throw new Error("401 Unauthorized");
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return await res.json();
}

// Fetches a binary response (PDF, DOCX) as a Blob with the same auth/401
// handling as request()/uploadRequest() — used for the tailoring dialog's
// PDF previews and PDF/DOCX export, where <embed src> can't carry an
// Authorization header so the bytes have to be fetched here and turned
// into an objectURL client-side.
async function blobRequest(path, options = {}) {
  const { method = "GET", body } = options;
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${BASE_URL}${path}`, { method, headers, body });
  if (res.status === 401) {
    clearToken();
    if (onUnauthorized) onUnauthorized();
    throw new Error("401 Unauthorized");
  }
  if (!res.ok) {
    const errBody = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${errBody}`);
  }
  return await res.blob();
}

// Triggers a real file download from a Blob — same browser-native pattern
// downloadTextFile() (Dashboard.jsx) already uses, generalized to accept
// a pre-made Blob instead of always constructing one from text.
export function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export const authApi = {
  register: (email, password) =>
    request("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
      skipAuthRedirect: true,
    }),
  login: (email, password) =>
    request("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
      skipAuthRedirect: true,
    }),
  me: () => request("/api/auth/me", { skipAuthRedirect: true }),
};

export const api = {
  triggerRun: (minAtsScore = 50) =>
    request(`/api/pipeline/run?min_ats_score=${encodeURIComponent(minAtsScore)}`, {
      method: "POST",
    }),
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
  uploadResume: (file) => {
    const formData = new FormData();
    formData.append("file", file);
    return uploadRequest("/api/profile/upload", formData);
  },

  getImprovement: () => request("/api/improvement"),
  getModelStatus: () => request("/api/model-status"),

  tailorResume: (dedupeKey) =>
    request("/api/jobs/tailor-resume", {
      method: "POST",
      body: JSON.stringify({ dedupe_key: dedupeKey }),
    }),
  rescoreTailored: (dedupeKey, headline, summary) =>
    request("/api/jobs/rescore-tailored", {
      method: "POST",
      body: JSON.stringify({ dedupe_key: dedupeKey, headline, summary }),
    }),

  // Interactive two-pane tailoring dialog (chat-driven editing agent).
  startTailorSession: (dedupeKey) =>
    request("/api/tailor-chat/start", {
      method: "POST",
      body: JSON.stringify({ dedupe_key: dedupeKey }),
    }),
  sendTailorMessage: (sessionId, message, targetSectionId) =>
    request("/api/tailor-chat/message", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, message, target_section_id: targetSectionId || null }),
    }),
  getOriginalFileBlob: () => blobRequest("/api/profile/original-file"),
  getTailorPreviewBlob: (sessionId, version) =>
    blobRequest(`/api/tailor-chat/preview-pdf/${encodeURIComponent(sessionId)}?v=${version}`),
  exportTailoredResume: (sessionId, format) =>
    blobRequest("/api/tailor-chat/export", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, format }),
    }),
  deleteTailorSession: (sessionId) =>
    request(`/api/tailor-chat/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),
};
