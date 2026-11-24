import { shortDate } from "../lib/stats.js";

// Scrolls: the list grows with every account added, the page should not.
export default function AccountsBox({ rows, slotOf }) {
  if (!rows.length) {
    return <p className="empty">No accounts yet — run an extraction first.</p>;
  }

  return (
    <div className="scroll-box" role="list">
      {rows.map((row) => (
        <div className="usage-row" key={row.handle} role="listitem">
          <i className="swatch" style={{ background: `var(--acct-${slotOf(row.handle)})` }} />
          <span className="who">@{row.handle}</span>
          <span className="times" title={`${row.runs} run(s) in view`}>
            {row.runs} run{row.runs === 1 ? "" : "s"}
          </span>
          <span className="sub">
            {row.posts} posts{row.last ? ` · last ${shortDate(row.last)}` : ""}
          </span>
        </div>
      ))}
    </div>
  );
}
