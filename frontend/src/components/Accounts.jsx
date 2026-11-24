// A handle X does not recognise is struck through in red, so it is obvious
// before a run wastes a browser on it.
export default function Accounts({ accounts, selected, onToggle, onRemove, onClear, state = {} }) {
  return (
    <div className="col">
      <h2 className="col-title">
        Accounts
        <span className="badge">{accounts.length}</span>
        {accounts.length > 0 && (
          <button type="button" className="btn-clear right" onClick={onClear}>
            Clear
          </button>
        )}
      </h2>

      {accounts.length === 0 ? (
        <p className="empty">No accounts yet — add a handle on the left.</p>
      ) : (
        // Capped and scrollable: twenty accounts must not become a ladder.
        <div className="acct-list">
          {accounts.map((a) => (
            <div className="acct-line" key={a.handle}>
              <input
                type="checkbox"
                checked={selected.includes(a.handle)}
                onChange={() => onToggle(a.handle)}
                aria-label={`Include @${a.handle}`}
              />
              <span className={"h" + (state[a.handle] === "missing" ? " missing" : "")}
                    title={state[a.handle] === "missing"
                      ? "X does not recognise this account"
                      : undefined}>
                @{a.handle}
              </span>
              <button
                type="button"
                className="x"
                onClick={() => onRemove(a.handle)}
                aria-label={`Remove @${a.handle}`}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
