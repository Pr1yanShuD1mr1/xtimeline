import { useEffect, useRef } from "react";

export default function Log({ lines, onClear }) {
  const boxRef = useRef(null);

  useEffect(() => {
    const el = boxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  return (
    <div className="col">
      <h2 className="col-title">
        Outbox
        {onClear && lines.length > 0 && (
          <button type="button" className="btn-clear right" onClick={onClear}>
            Clear
          </button>
        )}
      </h2>

      <div className="log" ref={boxRef} role="log" aria-live="polite">
        {lines.length === 0 ? (
          <div className="log-empty">
            nothing yet. press Extract and the run is narrated here:
            {"\n"}session age, each account as it is fetched, failures, and what
            to try next.
          </div>
        ) : (
          lines.map((l) => (
            <div key={l.id} className={"log-line " + l.level}>
              <span className="log-time">{l.at}</span>
              <span className="log-text">{l.text}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
