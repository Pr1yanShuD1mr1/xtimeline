import { shortDate } from "../lib/stats.js";

function niceMax(value) {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const scaled = value / magnitude;
  const steps = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
  return (steps.find((s) => scaled <= s) ?? 10) * magnitude;
}

// No legend: the accounts box beside it is the key, and repeating the names
// here just adds a second place to keep in sync.
export default function VolumeChart({ series, accounts }) {
  const W = 780;
  const H = 300;
  const padL = 42;
  const padR = 10;
  const padT = 14;
  const padB = 30;

  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const max = niceMax(Math.max(...series.map((d) => d.total), 1));
  const bw = innerW / Math.max(1, series.length);
  const gap = bw > 10 ? 2 : 1;

  const ticks = [0, 0.25, 0.5, 0.75, 1]
    .map((f) => ({ value: Math.round(max * f), y: padT + innerH - innerH * f }))
    .filter((t, i, all) => all.findIndex((o) => o.value === t.value) === i);

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label="Posts per day, stacked by account">
        {ticks.map((t) => (
          <g key={t.value}>
            <line x1={padL} x2={W - padR} y1={t.y} y2={t.y} className="grid-line" />
            <text x={padL - 8} y={t.y + 3} textAnchor="end">{t.value}</text>
          </g>
        ))}

        {series.map((day, i) => {
          const x = padL + i * bw + gap / 2;
          const width = Math.max(1, bw - gap);

          if (!day.hasData) {
            return (
              <line key={day.day} x1={x + width / 2} x2={x + width / 2}
                    y1={padT + innerH - 4} y2={padT + innerH}
                    stroke="var(--muted)" strokeWidth="1" opacity="0.5" />
            );
          }

          let cursor = padT + innerH;
          return (
            <g key={day.day}>
              <title>{`${day.day} · ${day.total} posts`}</title>
              {accounts.map((a) => {
                const value = day.byAccount[a.handle] || 0;
                if (!value) return null;
                const height = (value / max) * innerH;
                cursor -= height;
                return (
                  <rect key={a.handle} className="bar" x={x} y={cursor}
                        width={width} height={Math.max(1, height)}
                        fill={`var(--acct-${a.slot})`} />
                );
              })}
            </g>
          );
        })}

        <line className="baseline" x1={padL} x2={W - padR}
              y1={padT + innerH} y2={padT + innerH} />

        {series.map((day, i) =>
          i % Math.ceil(series.length / 7) === 0 ? (
            <text key={day.day} x={padL + i * bw + bw / 2} y={H - 10}
                  textAnchor="middle">{shortDate(day.day)}</text>
          ) : null
        )}
      </svg>
    </div>
  );
}
