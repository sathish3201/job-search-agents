import { createContext, useContext, useEffect, useState } from "react";
import { authApi, clearToken, getToken, setToken, setUnauthorizedHandler } from "../api/client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  // "checking" until the initial /api/auth/me validation (if a token is
  // stored) resolves — RequireAuth waits on this instead of briefly
  // flashing the login page for a user who actually has a valid session.
  const [status, setStatus] = useState("checking");

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      setStatus("anonymous");
    });

    const token = getToken();
    if (!token) {
      setStatus("anonymous");
      return;
    }
    authApi
      .me()
      .then((res) => {
        setUser(res);
        setStatus("authenticated");
      })
      .catch(() => {
        clearToken();
        setUser(null);
        setStatus("anonymous");
      });
  }, []);

  async function login(email, password) {
    const res = await authApi.login(email, password);
    setToken(res.access_token);
    setUser(res.user);
    setStatus("authenticated");
  }

  async function register(email, password) {
    const res = await authApi.register(email, password);
    setToken(res.access_token);
    setUser(res.user);
    setStatus("authenticated");
  }

  function logout() {
    clearToken();
    setUser(null);
    setStatus("anonymous");
  }

  return (
    <AuthContext.Provider value={{ user, status, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
