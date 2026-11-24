import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import VolumeChart from "../components/VolumeChart.jsx";
import StoreBox from "../components/StoreBox.jsx";
import AccountsBox from "../components/AccountsBox.jsx";
import PostHistory from "../components/PostHistory.jsx";
import { useArchive, useStats } from "../lib/archive.js";
import { useAuth } from "../auth/AuthContext.jsx";
import { api } from "../api.js";
import { dailySeriesFromStats } from "../lib/stats.js";

const RANGES = [
  [7, "7d"],
  [30, "30d"],
  [0, "All"],
];

// Reads the archive, so it works on a fresh browser with nothing running.
export default function DashboardPage() {
  const { posts, jobs, loading, error, included } = useArchive();
  const { expiresAt } = useAuth();

  const [range, setRange] = useState(30);
  const [health, setHealth] = useState(null);
  const [session, setSession] = useState(null);

  // After `range`: hooks cannot read a const before its declaration.
  const { stats } = useStats(range);

  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
    api.session().then(setSession).catch(() => {});
  }, []);

  const today = useMemo(() => new Date(), []);

  // Accounts come from the whole archive, not just the runs whose posts we
  // loaded; the palette slot follows volume.
  const accounts = useMemo(
    () => (stats?.accounts || []).map((a, i) => ({ ...a, slot: (i % 6) + 1 })),
    [stats]
  );

  const slotOf = (handle) =>
    accounts.find((a) => a.handle === handle)?.slot || 1;

  const series = useMemo(
    () => dailySeriesFromStats(stats?.daily, range || 30, today),
    [stats, range, today]
  );

  if (loading) {
    return <p className="empty">Reading the archive…</p>;
  }

  if (error) {
    return (
      <div className="banner warn">
        Could not read the archive — {error}.{" "}
        <Link to="/">Back to the workbench</Link>
      </div>
    );
  }

  if (!jobs.length) {
    return (
      <div className="banner">
        Nothing collected yet. <Link to="/">Run a scrape from the workbench</Link>{" "}
        and this page fills in.
      </div>
    );
  }

  return (
    <>
      <div className="dash-grid">
        <section className="panel col-8">
          <div className="panel-head">
            Posts per day, by account
            <span className="right">
              <span className="range" role="group" aria-label="Time window">
                {RANGES.map(([value, label]) => (
                  <button key={value} type="button" aria-pressed={range === value}
                          onClick={() => setRange(value)}>
                    {label}
                  </button>
                ))}
              </span>
              <span>zero-based</span>
            </span>
          </div>
          <VolumeChart series={series} accounts={accounts} />
        </section>

        <div className="col-4 right-col">
          <section className="boxed">
            <div className="boxed-head">
              Store &amp; session
              <span className="right">live</span>
            </div>
            <StoreBox health={health} session={session} expiresAt={expiresAt} />
          </section>

          <section className="boxed">
            <div className="boxed-head">
              Accounts extracted
              <span className="right">times used</span>
            </div>
            <AccountsBox rows={accounts} slotOf={slotOf} />
          </section>
        </div>

        <section className="panel col-12">
          <PostHistory posts={posts} accounts={accounts} today={today}
                       counts={stats?.accounts} />
        </section>
      </div>

      <p className="panel-note">
        Counts cover the whole archive — {stats?.runs ?? "…"} run
        {stats?.runs === 1 ? "" : "s"} and {stats?.posts ?? "…"} posts, live and
        stored. The cards below show the most recent {included} run
        {included === 1 ? "" : "s"} of {jobs.length}; older runs open from History.
      </p>
    </>
  );
}
