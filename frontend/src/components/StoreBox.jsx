import { clock } from "../lib/stats.js";

// The operational facts a scrape depends on — the panel that answers most
// "why did it fail" questions before they are asked.
export default function StoreBox({ health, session, expiresAt }) {
  if (!health && !session) return <p className="empty">Checking…</p>;

  const ageMinutes = session?.state_file_age_minutes;
  const hours = typeof ageMinutes === "number" ? Math.floor(ageMinutes / 60) : null;
  const stale = typeof ageMinutes === "number" && ageMinutes > 60 * 12;

  return (
    <>
      <dl className="facts">
        <dt>Extractor</dt>
        <dd>{health?.extractor || "—"}</dd>

        <dt>History store</dt>
        <dd className={health?.store === "mongo" ? "ok" : "warn"}>
          {health?.store || "—"}
          {health?.store_db ? ` · ${health.store_db}` : ""}
        </dd>

        <dt>Cluster</dt>
        <dd>{health?.store_host || "local"}</dd>

        <dt>X session</dt>
        <dd className={stale ? "warn" : ""}>
          {hours === null
            ? session?.saved ? "saved" : "not saved"
            : `cookies ${hours}h old`}
        </dd>

        <dt>Token</dt>
        <dd>
          {expiresAt
            ? clock(new Date(expiresAt * 1000).toISOString())
            : "—"}
        </dd>
      </dl>

      <p className="panel-note">
        {health?.store === "memory"
          ? "History is memory-only — past runs will not survive a restart."
          : stale
          ? "The saved session is old enough that a login wall is likely — re-run save_session.py."
          : "Session and store look healthy."}
      </p>
    </>
  );
}
