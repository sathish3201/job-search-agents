import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./App.css";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Applications from "./pages/Applications";
import Profile from "./pages/Profile";
import Improvement from "./pages/Improvement";
import ProfileDrafts from "./pages/ProfileDrafts";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="applications" element={<Applications />} />
          <Route path="profile" element={<Profile />} />
          <Route path="improvement" element={<Improvement />} />
          <Route path="drafts" element={<ProfileDrafts />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
