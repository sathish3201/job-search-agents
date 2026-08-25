import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

const DISMISSED_KEY = "job_search_agent_onboarding_dismissed";

const STEPS = [
  {
    title: "1. Upload your resume",
    body: "Go to Profile and upload a PDF, DOCX, TXT, or MD resume (up to 5MB). It's parsed into a structured profile — skills, target roles, experience.",
  },
  {
    title: "2. Run a search",
    body: "From the Dashboard, click \"Run search\". The agent searches job boards, ranks each listing against your resume, and checks it against an ATS score.",
  },
  {
    title: "3. Review ranked jobs",
    body: "Once a run finishes, ranked jobs appear with a fit score and ATS score. Higher-fit jobs are the ones worth applying to.",
  },
  {
    title: "4. Track applications",
    body: "Jobs you act on show up under Applications, where you can update status, add notes, or discard ones you're not pursuing.",
  },
];

// Fixed static copy, not LLM-generated — instant, free, and doesn't depend
// on the LLM tunnel being reachable just to show a first-run guide.
export default function OnboardingModal({ onClose }) {
  const navigate = useNavigate();

  function dismiss() {
    localStorage.setItem(DISMISSED_KEY, "true");
    onClose();
  }

  function dismissAndGoToProfile() {
    dismiss();
    navigate("/profile");
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
      onClick={dismiss}
    >
      <div
        className="card"
        style={{ maxWidth: 480, margin: 0 }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2>Welcome to Job Search Agent</h2>
        <p className="muted">
          This tool finds and ranks job listings against your resume, and helps you track
          applications. Here's how to get started:
        </p>
        {STEPS.map((step) => (
          <div key={step.title} style={{ marginBottom: "0.85rem" }}>
            <strong>{step.title}</strong>
            <p className="muted" style={{ margin: "0.25rem 0 0" }}>
              {step.body}
            </p>
          </div>
        ))}
        <div style={{ display: "flex", gap: "0.75rem", marginTop: "1.25rem" }}>
          <button type="button" onClick={dismissAndGoToProfile}>
            Upload my resume now
          </button>
          <button type="button" onClick={dismiss}>
            Got it, dismiss
          </button>
        </div>
      </div>
    </div>
  );
}

export function useOnboardingDismissed() {
  const [dismissed, setDismissed] = useState(true);

  useEffect(() => {
    setDismissed(localStorage.getItem(DISMISSED_KEY) === "true");
  }, []);

  return dismissed;
}
