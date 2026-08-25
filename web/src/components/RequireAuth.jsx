import { Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function RequireAuth({ children }) {
  const { status } = useAuth();

  if (status === "checking") {
    return <p className="muted">Loading...</p>;
  }
  if (status === "anonymous") {
    return <Navigate to="/login" replace />;
  }
  return children;
}
