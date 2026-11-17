import { useEffect, useState } from "react";
import { QRCodeCanvas } from "qrcode.react";
import Notice from "./Notice";
import SelfieCell from "./SelfieCell";
import { facultyApi } from "../lib/facultyApi";
import { formatCountdown, timerTone } from "../hooks/useCountdown";

/**
 * Faculty-side face enrollment: shows the short-lived code (and QR) students
 * need to register a face, plus a form to reset a student's registered face.
 */
export default function EnrollmentPanel({ enrollment, secondsLeft, link, onReset, onClose }) {
  const [usn, setUsn] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  // Registrations waiting for approval (text only; photos load on request).
  const [roster, setRoster] = useState(null);
  const [busyUsn, setBusyUsn] = useState("");

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;

    const pull = async () => {
      if (inFlight || document.hidden) return;
      inFlight = true;
      try {
        const response = await facultyApi.get(`/enrollment/status/${enrollment.className}`);
        if (!cancelled) setRoster(response.data);
      } catch {
        /* keep the last list */
      } finally {
        inFlight = false;
      }
    };

    pull();
    const timer = setInterval(pull, 4000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enrollment.className]);

  const decide = async (usn, approve) => {
    setBusyUsn(usn);
    try {
      if (approve) await facultyApi.post(`/face/approve/${usn}`);
      else await facultyApi.delete(`/face/delete/${usn}`);
      const response = await facultyApi.get(`/enrollment/status/${enrollment.className}`);
      setRoster(response.data);
    } catch (error) {
      console.error(error);
    } finally {
      setBusyUsn("");
    }
  };

  const lock = enrollment.networkLock;
  const lockText = !lock
    ? "any network"
    : lock.mode === "any"
      ? "any network (testing)"
      : (lock.networks || []).join(", ") || "no network recorded";

  const expired = secondsLeft === 0;
  const tone = expired ? "bad" : timerTone(secondsLeft ?? 0);
  const progress = enrollment.total
    ? Math.min(100, ((secondsLeft ?? 0) / enrollment.total) * 100)
    : 0;

  const submit = async (event) => {
    event.preventDefault();
    const clean = usn.trim().toUpperCase();
    if (!clean || busy) return;

    setBusy(true);
    setResult(null);
    const outcome = await onReset(clean);
    setResult(outcome);
    if (outcome.type === "success") setUsn("");
    setBusy(false);
  };

  return (
    <div className="section">
      <div className="section-head">
        <h2 className="section-title">Student Enrollment — {enrollment.className}</h2>
        <button type="button" className="ghost-btn" onClick={onClose}>
          Close
        </button>
      </div>

      <div className="enroll-panel">
        <div className="enroll-card enroll-card--center">
          <h3>Enrollment code</h3>
          <p className="qr-desc">
            Students open <strong>Face Registration</strong> and enter this code, or scan the QR.
          </p>

          <div
            className={`enroll-code ${expired ? "enroll-code--expired" : ""}`}
            aria-label="Enrollment code"
          >
            {enrollment.code}
          </div>

          <div className={`session-timer session-timer--${tone}`} role="timer">
            <div className="session-timer__row">
              <span className="session-timer__label">{expired ? "Code expired" : "Valid for"}</span>
              <strong className="session-timer__time">{formatCountdown(secondsLeft)}</strong>
            </div>
            <div className="session-timer__bar">
              <i style={{ width: `${progress}%` }} />
            </div>
          </div>

          <div className={`qr-wrapper ${expired ? "qr-wrapper--expired" : ""}`}>
            <QRCodeCanvas value={link} size={160} level="M" includeMargin={true} />
          </div>
          <p className="qr-link__note">
            Only students of {enrollment.className} on <strong>{lockText}</strong> can use this
            code, one student per phone. Generating a new code replaces this one.
          </p>
        </div>

        <form className="enroll-card" onSubmit={submit}>
          <h3>Reset a student&apos;s face</h3>
          <p className="qr-desc">
            Use this when a student registered the wrong photo. Once reset, they can register
            again with a code.
          </p>

          {result && <Notice type={result.type}>{result.text}</Notice>}

          <label className="ft-label" htmlFor="reset-usn">
            University Seat Number (USN)
          </label>
          <div className="ft-row">
            <input
              id="reset-usn"
              className="ft-input"
              type="text"
              placeholder="e.g. 1BM23IS001"
              value={usn}
              onChange={(e) => setUsn(e.target.value.toUpperCase())}
            />
            <button type="submit" className="secondary-btn" disabled={busy || !usn.trim()}>
              {busy ? "Resetting…" : "Reset face"}
            </button>
          </div>
        </form>
      </div>

      <div className="enroll-card enroll-approvals">
        <div className="live-card__head">
          <h3>Registrations</h3>
          <span className="live-count">
            {roster ? (
              <>
                <strong>{roster.approved}</strong> approved · <strong>{roster.pending.length}</strong> waiting · {roster.total} in class
              </>
            ) : (
              "Loading…"
            )}
          </span>
        </div>
        <p className="qr-desc">
          Check that each photo is really that student before approving. A student can't check
          in until their face is approved.
        </p>

        {roster && roster.pending.length === 0 ? (
          <p className="qr-desc live-empty">Nothing waiting for approval.</p>
        ) : (
          <ul className="live-list">
            {(roster?.pending || []).map((item) => (
              <li key={item.usn} className="live-row approve-row">
                <span className="live-row__who">
                  <strong>{item.name || item.usn}</strong>
                  <small>{item.usn}</small>
                </span>
                <span className="live-row__flags">
                  {item.capture_mode !== "live" && <span className="flag-badge">Not live camera</span>}
                </span>
                <span className="live-row__actions">
                  <SelfieCell sessionId="" usn={item.usn} path={`/face/photo/${item.usn}`} />
                  <button
                    type="button"
                    className="action-btn live-btn"
                    disabled={busyUsn === item.usn}
                    onClick={() => decide(item.usn, true)}
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    className="secondary-btn live-btn"
                    disabled={busyUsn === item.usn}
                    onClick={() => decide(item.usn, false)}
                  >
                    Reject
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
