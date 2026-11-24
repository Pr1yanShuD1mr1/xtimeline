import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext.jsx";
import { useTheme } from "../theme/ThemeContext.jsx";
import { api } from "../api.js";

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20.5 14.8A8.6 8.6 0 0 1 9.2 3.5a8.6 8.6 0 1 0 11.3 11.3Z" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
         strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2.6v2.1M12 19.3v2.1M2.6 12h2.1M19.3 12h2.1M5.3 5.3l1.5 1.5M17.2 17.2l1.5 1.5M18.7 5.3l-1.5 1.5M6.8 17.2l-1.5 1.5" />
    </svg>
  );
}

export default function Masthead() {
  const { signOut } = useAuth();
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();

  function handleSignOut() {
    api.logout();          // stateless on the server; this is just tidy
    signOut("Signed out.");
    navigate("/login", { replace: true });
  }

  return (
    <header className="masthead">
      <div className="brand">
        <span className="tile" aria-hidden="true">X</span>
        <h1 className="wordmark">Scraper</h1>
      </div>

      <nav className="masthead-nav" aria-label="Main">
        <NavLink to="/dashboard" className="navlink">Dashboard</NavLink>
        <NavLink to="/" end className="navlink">Workbench</NavLink>
        <NavLink to="/history" className="navlink">History</NavLink>

        <button
          type="button"
          className="theme-btn"
          onClick={toggle}
          aria-label={theme === "day" ? "Switch to night edition" : "Switch to day edition"}
          title={theme === "day" ? "Night edition" : "Day edition"}
        >
          {theme === "day" ? <MoonIcon /> : <SunIcon />}
        </button>

        <button type="button" className="signout" onClick={handleSignOut}>
          Sign out
        </button>
      </nav>
    </header>
  );
}
