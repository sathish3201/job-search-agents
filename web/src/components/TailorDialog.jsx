import { useEffect, useRef, useState } from "react";
import { api, downloadBlob } from "../api/client";

// Full-screen two-pane interactive resume-tailoring dialog: the user's
// real original PDF on the left (ground truth — see the plan's scope
// decision that the right pane is a clean template, not a clone of the
// original's design), a chat-driven, agent-edited tailored PDF preview on
// the right, plus an outline list for click-to-target editing.
export default function TailorDialog({ dedupeKey, onClose }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [sessionId, setSessionId] = useState(null);
  const [draft, setDraft] = useState(null);
  const [messages, setMessages] = useState([]);
  const [originalFileUrl, setOriginalFileUrl] = useState(null);
  const [tailoredPreviewUrl, setTailoredPreviewUrl] = useState(null);
  const [messageText, setMessageText] = useState("");
  const [targetSectionId, setTargetSectionId] = useState(null);
  const [sending, setSending] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [exporting, setExporting] = useState(false);
  const transcriptRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    async function start() {
      try {
        const res = await api.startTailorSession(dedupeKey);
        if (cancelled) return;
        setSessionId(res.session_id);
        setDraft(res.draft);
        setMessages(res.messages || []);

        try {
          const blob = await api.getOriginalFileBlob();
          if (!cancelled) setOriginalFileUrl(URL.createObjectURL(blob));
        } catch {
          // No original file on record (txt/md upload) — left pane falls
          // back to a message instead of an embed.
        }

        const previewBlob = await api.getTailorPreviewBlob(res.session_id, res.draft.version);
        if (!cancelled) setTailoredPreviewUrl(URL.createObjectURL(previewBlob));
      } catch (err) {
        if (!cancelled) setError(err.message || "Could not start tailoring session.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    start();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dedupeKey]);

  useEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
    }
  }, [messages]);

  async function handleClose() {
    if (sessionId) {
      api.deleteTailorSession(sessionId).catch(() => {});
    }
    onClose();
  }

  async function handleSend() {
    if (!messageText.trim() || sending) return;
    const text = messageText;
    const target = targetSectionId;
    setMessageText("");
    setTargetSectionId(null);
    setSending(true);
    setMessages((prev) => [...prev, { role: "user", content: text, target_section_id: target }]);
    try {
      const res = await api.sendTailorMessage(sessionId, text, target);
      setMessages((prev) => [...prev, { role: "agent", content: res.reply }]);
      setDraft(res.draft);
      setConfirmed(false);
      const previewBlob = await api.getTailorPreviewBlob(sessionId, res.draft.version);
      setTailoredPreviewUrl(URL.createObjectURL(previewBlob));
    } catch (err) {
      setMessages((prev) => [...prev, { role: "agent", content: `Error: ${err.message}` }]);
    } finally {
      setSending(false);
    }
  }

  async function handleExport(format) {
    setExporting(true);
    try {
      const blob = await api.exportTailoredResume(sessionId, format);
      downloadBlob(`tailored-resume-${(draft.target_title || "resume").replace(/\s+/g, "-").toLowerCase()}.${format}`, blob);
    } catch (err) {
      setError(err.message || "Export failed.");
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={handleClose}>
      <div className="modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="tailor-dialog-header">
          <h2>Tailor Resume</h2>
          <button onClick={handleClose} className="icon-btn" aria-label="Close">
            ×
          </button>
        </div>

        {loading && <p className="muted">Starting tailoring session…</p>}
        {error && <p className="error-text">{error}</p>}

        {!loading && draft && (
          <div className="tailor-dialog-body">
            <div className="tailor-pane">
              <div className="tailor-pane-header">Your original resume</div>
              {originalFileUrl ? (
                <embed src={originalFileUrl} type="application/pdf" className="tailor-pdf-embed" />
              ) : (
                <p className="muted" style={{ padding: "1rem" }}>
                  No original file on record for this account (only PDF/DOCX uploads keep a copy).
                </p>
              )}
            </div>

            <div className="tailor-right-pane">
              <div className="tailor-pane-header">
                Tailored preview — ATS score {draft.ats_score}/100 (our resume template, not a copy of your
                original's design)
              </div>
              {tailoredPreviewUrl && (
                <embed src={tailoredPreviewUrl} type="application/pdf" className="tailor-pdf-embed" style={{ flex: "0 0 45%" }} />
              )}

              <div className="tailor-outline">
                {draft.sections.map((s) => (
                  <div
                    key={s.section_id}
                    className={`tailor-outline-row${targetSectionId === s.section_id ? " active" : ""}`}
                    onClick={() => setTargetSectionId(s.section_id)}
                  >
                    {s.text.slice(0, 70) || `(${s.section_type})`}
                  </div>
                ))}
              </div>

              {targetSectionId && (
                <div className="tailor-target-chip">
                  Targeting: {targetSectionId}
                  <button type="button" onClick={() => setTargetSectionId(null)} aria-label="Clear target">
                    ×
                  </button>
                </div>
              )}

              <div className="tailor-chat-transcript" ref={transcriptRef}>
                {messages.length === 0 && (
                  <p className="muted">
                    Click a line above to target it, or just describe what you'd like changed — e.g. "make the
                    summary mention Terraform" or "add a bullet about my AWS work".
                  </p>
                )}
                {messages.map((m, i) => (
                  <div key={i} className={`tailor-chat-message ${m.role === "user" ? "user" : "agent"}`}>
                    {m.content}
                  </div>
                ))}
                {sending && <div className="tailor-chat-message agent muted">Thinking…</div>}
              </div>

              <div className="tailor-chat-input-row">
                <input
                  type="text"
                  value={messageText}
                  onChange={(e) => setMessageText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleSend()}
                  placeholder="Describe an edit…"
                  disabled={sending}
                />
                <button type="button" onClick={handleSend} disabled={sending || !messageText.trim()}>
                  Send
                </button>
              </div>

              <div style={{ padding: "0.75rem", borderTop: "1px solid var(--border)" }}>
                {!confirmed ? (
                  <button type="button" className="primary-btn" onClick={() => setConfirmed(true)}>
                    Accept this tailoring
                  </button>
                ) : (
                  <div style={{ display: "flex", gap: "0.5rem" }}>
                    <button type="button" className="primary-btn" disabled={exporting} onClick={() => handleExport("pdf")}>
                      {exporting ? "Exporting…" : "Download PDF"}
                    </button>
                    <button type="button" className="primary-btn" disabled={exporting} onClick={() => handleExport("docx")}>
                      {exporting ? "Exporting…" : "Download DOCX"}
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
