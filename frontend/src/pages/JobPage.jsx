import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Results from "../components/Results.jsx";
import { api, subscribe, debugUrl } from "../api.js";

// The fetched page: one job and its posts, on its own route.
export default function JobPage() {
  const { jobId } = useParams();
  const [job, setJob] = useState(null);
  const [tweets, setTweets] = useState([]);
  const [error, setError] = useState("");
  const unsubscribe = useRef(null);

  useEffect(() => {
    let alive = true;
    setJob(null);
    setTweets([]);
    setError("");

    async function load() {
      try {
        const snap = await api.getJob(jobId);
        if (!alive) return;
        setJob(snap);

        if (snap.status === "running" || snap.status === "queued") {
          unsubscribe.current = subscribe(jobId, setJob, async () => {
            const final = await api.getJob(jobId).catch(() => null);
            if (final) setJob(final);
            api.getResult(jobId)
              .then((res) => alive && setTweets(res.tweets || []))
              .catch(() => {});
          });
        } else {
          const res = await api.getResult(jobId);
          if (alive) setTweets(res.tweets || []);
        }
      } catch (err) {
        if (alive) setError(err.message || "could not load this job");
      }
    }

    load();
    return () => {
      alive = false;
      if (unsubscribe.current) unsubscribe.current();
    };
  }, [jobId]);

  if (error) {
    return (
      <>
        <div className="banner err">{error}</div>
        <p className="empty"><Link to="/history">Back to history</Link></p>
      </>
    );
  }

  if (!job) return <p className="empty">Loading job {jobId}…</p>;

  const running = job.status === "running" || job.status === "queued";
  const pct = Math.max(0, Math.min(100, job.progress || 0));

  return (
    <>
      {running && (
        <div className="status-strip running">
          <span className="dot" />
          <span>{job.message || "running…"}</span>
          <span className="pct">{pct}%</span>
          <div className="bar"><i style={{ width: `${pct}%` }} /></div>
        </div>
      )}

      {job.status === "error" && <div className="banner err">{job.error || job.message}</div>}

      {job.status === "done" && tweets.length === 0 && (
        <div className="banner warn">
          <span>
            No posts in this range{job.note ? `: ${job.note}` : ""}.{" "}
            <a href={debugUrl(job.id)} target="_blank" rel="noreferrer">
              View debug screenshot
            </a>
          </span>
        </div>
      )}

      {!running && <Results job={job} tweets={tweets} note={tweets.length ? job.note : ""} />}
    </>
  );
}
