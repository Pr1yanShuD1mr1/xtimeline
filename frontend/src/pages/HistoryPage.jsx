import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export default function HistoryPage() {
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const res = await api.listJobs();
      setJobs(res.jobs || []);
      setError("");
    } catch (err) {
      setError(err.message || "could not load the ledger");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // Jobs live in the backend's memory; a slow poll keeps the ledger current.
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const running = useMemo(
    () => jobs.filter((j) => j.status === "running" || j.status === "queued").length,
    [jobs]
  );

  return (
    <div className="deck results">
      <div className="col" style={{ gridColumn: "1 / -1", paddingRight: 0 }}>
        <h2 className="col-title">
          Job ledger
          <span className="right">
            {jobs.length} {jobs.length === 1 ? "entry" : "entries"}{running ? ` · ${running} running` : ""}
          </span>
          <button type="button" className="btn-clear" onClick={load}>
            Refresh
          </button>
        </h2>

        {error && <div className="banner err">{error}</div>}

        {loading ? (
          <p className="empty">Loading…</p>
        ) : jobs.length === 0 ? (
          <p className="empty">
            Nothing here yet — start an extraction and it will show up.
          </p>
        ) : (
          <table className="ledger">
            <thead>
              <tr>
                <th style={{ width: 56 }}>No.</th>
                <th style={{ width: 140 }}>ID</th>
                <th style={{ width: 190 }}>Started</th>
                <th>Accounts</th>
                <th style={{ width: 96 }}>Page</th>
                <th style={{ width: 92 }}>Status</th>
                <th style={{ width: 66 }}>Posts</th>
                <th style={{ width: 84 }}>Open</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job, i) => (
                <tr key={job.id}>
                  <td>{String(jobs.length - i).padStart(2, "0")}</td>
                  <td>{job.id}</td>
                  <td>{when(job.started_at || job.created_at)}</td>
                  <td>{(job.handles || []).join(", ") || "—"}</td>
                  <td>{job.strategy || "—"}</td>
                  <td className={"status-" + job.status}>{job.status}</td>
                  <td>{job.collected}</td>
                  <td className="open">
                    <Link className="open-btn" to={`/jobs/${job.id}`}>open</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
