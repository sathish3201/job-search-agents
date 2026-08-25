import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import StatusBadge from "./StatusBadge";
import OnboardingModal, { useOnboardingDismissed } from "./OnboardingModal";
import { useAuth } from "../context/AuthContext";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/applications", label: "Applications" },
  { to: "/profile", label: "Profile" },
  { to: "/improvement", label: "Improvement" },
  { to: "/drafts", label: "Profile Drafts" },
  { to: "/profile-audit", label: "Profile Audit" },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const onboardingDismissed = useOnboardingDismissed();
  const [showOnboarding, setShowOnboarding] = useState(false);

  useEffect(() => {
    if (!onboardingDismissed) setShowOnboarding(true);
  }, [onboardingDismissed]);

  function handleLogout() {
    logout();
    navigate("/login");
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">Job Search Agent</div>
        <nav className="nav">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <StatusBadge />
        {user && (
          <div className="nav" style={{ marginLeft: "1rem" }}>
            <span className="muted">{user.email}</span>
            <button type="button" onClick={handleLogout}>
              Log out
            </button>
          </div>
        )}
      </header>
      <main className="content">
        <Outlet />
      </main>
      {showOnboarding && <OnboardingModal onClose={() => setShowOnboarding(false)} />}
    </div>
  );
}
