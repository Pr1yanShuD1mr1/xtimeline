import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext.jsx";

// The gate. Anything wrapped in this is only rendered with a valid session;
// without one we redirect to /login and remember where we were trying to go,
// so signing in can drop the user back on the page they asked for.
export default function ProtectedRoute({ children }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "checking") {
    return (
      <div className="splash">
        <span className="spinner" aria-hidden="true" />
        Checking your session…
      </div>
    );
  }

  if (status !== "authed") {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return children;
}
