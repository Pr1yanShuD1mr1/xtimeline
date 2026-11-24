import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";

export default function LoginPage() {
  const { status, signIn, notice } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const from = location.state?.from;
  const target = from ? `${from.pathname}${from.search || ""}` : "/";

  if (status === "authed") return <Navigate to={target} replace />;

  async function submit(event) {
    event.preventDefault();
    if (!password.trim()) {
      setError("Enter the access password.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await signIn(password);
      navigate(target, { replace: true });
    } catch (err) {
      setError(err.message || "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">
          <span className="tile" aria-hidden="true">X</span>
          <div>
            <h1>Scraper</h1>
            <p>Timeline Extractor</p>
          </div>
        </div>

        {(notice || error) && (
          <div className="login-error">{error || notice}</div>
        )}

        <div>
          <label className="lbl" htmlFor="password">Access password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            autoFocus
            autoComplete="current-password"
            aria-label="Access password"
          />
        </div>

        <button type="submit" className="btn btn-dark" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <p className="note">
          This is the XTIMELINE_PASSWORD the backend was started with (default
          <code> changeme</code>). The password buys a short-lived bearer token;
          the token is what the protected routes check.
        </p>
      </form>
    </div>
  );
}
