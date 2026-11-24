import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, getToken, setToken } from "../api.js";

// Who is signed in, and how do we find out?
//
// status: "checking" -> we have a token and are asking the backend if it's still
//                       good (prevents a flash of the login page on refresh)
//         "authed"   -> good to go
//         "anon"     -> no token, or the backend rejected it

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setTokenState] = useState(() => getToken());
  const [status, setStatus] = useState(() => (getToken() ? "checking" : "anon"));
  const [expiresAt, setExpiresAt] = useState(null);
  const [notice, setNotice] = useState("");

  const signOut = useCallback((reason = "") => {
    setToken("");
    setTokenState("");
    setExpiresAt(null);
    setStatus("anon");
    setNotice(reason);
  }, []);

  // Validate a stored token once, so a refresh doesn't dump us on /login.
  useEffect(() => {
    if (!token) {
      setStatus("anon");
      return undefined;
    }
    let alive = true;
    setStatus("checking");
    api
      .me()
      .then((me) => {
        if (!alive) return;
        setExpiresAt(me.expires_at);
        setStatus("authed");
      })
      .catch(() => {
        if (!alive) return;
        setToken("");
        setTokenState("");
        setStatus("anon");
        setNotice("Your session expired — please sign in again.");
      });
    return () => {
      alive = false;
    };
  }, [token]);

  // Any 401 from anywhere in the app lands here.
  useEffect(() => {
    const onUnauthorized = () => signOut("Your session expired — please sign in again.");
    window.addEventListener("xtimeline:unauthorized", onUnauthorized);
    return () => window.removeEventListener("xtimeline:unauthorized", onUnauthorized);
  }, [signOut]);

  const signIn = useCallback(async (password) => {
    const res = await api.login(password); // throws ApiError on a bad password
    setToken(res.token);
    setTokenState(res.token);
    setExpiresAt(res.expires_at);
    setStatus("authed");
    setNotice("");
    return res;
  }, []);

  const value = { status, token, expiresAt, notice, signIn, signOut };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
