import SelfieCell from "./SelfieCell";

const describeLock = (lock) => {
  if (!lock) return "any network";
  if (lock.mode === "any") return "any network (testing)";
  return lock.networks?.length ? lock.networks.join(", ") : "no network recorded";
};

/**
 * Text-only live view of an open session: who has checked in, what is worth
 * a second look, and which networks were turned away. No images load here;
 * "Photo" fetches a single picture only when it is clicked.
 */
export default function LiveCheckIns({ sessionId, data, formatTime, onReject, onAllowNetwork, busyUsn }) {
  if (!data) {
    return (
      <div className="attendance-summary-card live-card">
        <h3>Live check-ins</h3>
        <p className="qr-desc">Waiting for the first update…</p>
      </div>
    );
  }

  const { check_ins: checkIns, present, held = 0, total, network_lock: lock, blocked, device_blocked: deviceBlocked } = data;

  return (
    <div className="attendance-summary-card live-card">
      <div className="live-card__head">
        <h3>Live check-ins</h3>
        <span className="live-count" aria-live="polite">
          <strong>{present}</strong> / {total} present
          {held > 0 && <span className="live-count__held"> · {held} to review</span>}
        </span>
      </div>

      <p className="live-network">
        Accepting check-ins from: <strong>{describeLock(lock)}</strong>
      </p>

      {blocked?.networks?.length > 0 && (
        <div className="live-blocked" role="status">
          <span>
            {blocked.count} attempt{blocked.count === 1 ? "" : "s"} blocked from another network:
          </span>
          {blocked.networks.map((network) => (
            <span className="live-blocked__item" key={network}>
              <code>{network}</code>
              <button type="button" className="ghost-btn live-btn" onClick={() => onAllowNetwork(network)}>
                Allow
              </button>
            </span>
          ))}
        </div>
      )}
      {deviceBlocked?.count > 0 && (
        <div className="live-blocked" role="status">
          <span>
            {deviceBlocked.count} check-in{deviceBlocked.count === 1 ? "" : "s"} blocked: a phone tried to
            check in another student
            {deviceBlocked.usns?.length ? ` (${deviceBlocked.usns.join(", ")})` : ""}.
          </span>
        </div>
      )}
      {blocked?.count > 0 && !blocked?.networks?.length && (
        <p className="live-network">{blocked.count} attempt{blocked.count === 1 ? "" : "s"} blocked from another network.</p>
      )}

      {checkIns.length === 0 ? (
        <p className="qr-desc live-empty">No one has checked in yet.</p>
      ) : (
        <ul className="live-list">
          {checkIns.map((item) => (
            <li key={item.usn} className={`live-row ${item.rejected ? "live-row--rejected" : ""} ${item.held ? "live-row--held" : ""}`}>
              <span className="live-row__time">{formatTime(item.marked_at)}</span>
              <span className="live-row__who">
                <strong>{item.name || item.usn}</strong>
                <small>{item.usn}</small>
              </span>
              <span className="live-row__flags">
                {item.flags.map((flag) => (
                  <span className="flag-badge" key={flag}>{flag}</span>
                ))}
              </span>
              <span className="live-row__actions">
                {item.has_selfie && <SelfieCell sessionId={sessionId} usn={item.usn} />}
                {item.held && (
                  <button
                    type="button"
                    className="action-btn live-btn"
                    disabled={busyUsn === item.usn}
                    onClick={() => onReject(item.usn, false)}
                  >
                    Confirm
                  </button>
                )}
                <button
                  type="button"
                  className={item.rejected ? "ghost-btn live-btn" : "secondary-btn live-btn"}
                  disabled={busyUsn === item.usn}
                  onClick={() => onReject(item.usn, !item.rejected)}
                >
                  {item.rejected ? "Restore" : "Reject"}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
