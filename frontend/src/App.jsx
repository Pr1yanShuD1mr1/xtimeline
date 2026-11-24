import { Link, Route, Routes } from "react-router-dom";
import ProtectedRoute from "./auth/ProtectedRoute.jsx";
import Layout from "./components/Layout.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import DashboardPage from "./pages/DashboardPage.jsx";
import WorkbenchPage from "./pages/WorkbenchPage.jsx";
import HistoryPage from "./pages/HistoryPage.jsx";
import JobPage from "./pages/JobPage.jsx";

function NotFound() {
  return (
    <div className="notfound">
      <h2>Nothing here</h2>
      <p className="empty">That page doesn&apos;t exist.</p>
      <p><Link to="/">Back to the workbench</Link></p>
    </div>
  );
}

// /login is public; everything else sits behind <ProtectedRoute>, which renders
// the masthead shell only for a valid session.
export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route index element={<WorkbenchPage />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="jobs/:jobId" element={<JobPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
