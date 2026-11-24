import { useEffect, useState } from "react";
import { api } from "../api.js";

// Loads the job list, then the posts of the most recent runs. Capped: a 60-run
// history would otherwise open 60 connections on page load.

export const MAX_JOBS = 20;

function normalise(tweet, job) {
  const metrics = tweet.metrics || {};
  const at = tweet.datetime || job.finished_at || job.created_at;
  return {
    ...tweet,
    at,
    day: String(at).slice(0, 10),
    extractedAt: job.finished_at || job.started_at || job.created_at || at,
    account: tweet.account || String(tweet.author || "").replace(/^@/, ""),
    likes: metrics.likes || 0,
    views: metrics.views || 0,
    replies: metrics.replies || 0,
    reposts: metrics.reposts || 0,
    bookmarks: metrics.bookmarks || 0,
  };
}

// Whole-archive totals from the server, so the boxes are not "in view" numbers.
export function useStats(days) {
  const [state, setState] = useState({ loading: true, error: "", stats: null });

  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true }));

    api.stats(days)
      .then((stats) => { if (alive) setState({ loading: false, error: "", stats }); })
      .catch((err) => {
        if (alive) setState({ loading: false, error: err.message, stats: null });
      });

    return () => { alive = false; };
  }, [days]);

  return state;
}

export function useArchive() {
  const [state, setState] = useState({
    loading: true, error: "", jobs: [], loadedJobs: [], posts: [], included: 0,
  });

  useEffect(() => {
    let alive = true;

    (async () => {
      try {
        const listing = await api.listJobs();
        const jobs = listing.jobs || [];
        const recent = jobs.slice(0, MAX_JOBS);

        const results = await Promise.all(
          recent.map((job) => api.getResult(job.id).catch(() => null))
        );

        const posts = [];
        results.forEach((result, i) => {
          if (!result) return;
          for (const tweet of result.tweets || []) {
            posts.push(normalise(tweet, recent[i]));
          }
        });

        if (alive) {
          // loadedJobs is the slice whose posts we actually have; every count
          // on the dashboard is scoped to it so the numbers agree.
          setState({
            loading: false, error: "", jobs, loadedJobs: recent, posts,
            included: recent.length,
          });
        }
      } catch (err) {
        if (alive) {
          setState((s) => ({ ...s, loading: false, error: err.message }));
        }
      }
    })();

    return () => {
      alive = false;
    };
  }, []);

  return state;
}
