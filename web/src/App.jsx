import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./App.css";
import Layout from "./components/Layout";
import RequireAuth from "./components/RequireAuth";
import { AuthProvider } from "./context/AuthContext";
import Dashboard from "./pages/Dashboard";
import Applications from "./pages/Applications";
import Profile from "./pages/Profile";
import Improvement from "./pages/Improvement";
import ProfileDrafts from "./pages/ProfileDrafts";
import ProfileAudit from "./pages/ProfileAudit";
import Login from "./pages/Login";
import Register from "./pages/Register";

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="login" element={<Login />} />
          <Route path="register" element={<Register />} />
          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route index element={<Dashboard />} />
            <Route path="applications" element={<Applications />} />
            <Route path="profile" element={<Profile />} />
            <Route path="improvement" element={<Improvement />} />
            <Route path="drafts" element={<ProfileDrafts />} />
            <Route path="profile-audit" element={<ProfileAudit />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
