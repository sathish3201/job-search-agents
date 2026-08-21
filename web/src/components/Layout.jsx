import { NavLink, Outlet } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/applications", label: "Applications" },
  { to: "/profile", label: "Profile" },
  { to: "/improvement", label: "Improvement" },
  { to: "/drafts", label: "Profile Drafts" },
];

export default function Layout() {
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
      </header>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
