import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import axios from "axios";
import { API_URL } from "../config";
import PageShell from "../components/PageShell";
import Notice from "../components/Notice";
import FileDrop from "../components/FileDrop";
import FaceScan from "../components/FaceScan";
import { CheckIcon, ScanIcon } from "../components/Icons";
import useCountdown, { formatCountdown } from "../hooks/useCountdown";
import { cameraAvailable } from "../lib/camera";
import { getStoredDevice, rememberDevice } from "../lib/deviceId";

const USN_KEY = "ft_usn";

const rememberedUsn = () => {
  try {
    return localStorage.getItem(USN_KEY) || "";
  } catch {
    return "";
  }
};

const rememberUsn = (usn) => {
  try {
    localStorage.setItem(USN_KEY, usn);
  } catch {
    /* private mode: just don't remember */
  }
};

/**
 * Attendance check-in. Reached only by scanning the session QR shown by
 * faculty (/mark?session=<id>&t=<token>). The QR token rotates every few
 * seconds, so the page immediately trades it for a short-lived check-in pass
 * and removes the token from the address bar.
 *
 * With a camera available, the page scans your face by itself (no photo
 * button): enter your USN once, then just look at the phone. Without one it
 * falls back to the phone's camera app and a manual submit.
 */
function MarkAttendanceInner() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const sessionId = params.get("session") || "";
  const token = params.get("t") || "";

  const [pass, setPass] = useState("");
  const [passStatus, setPassStatus] = useState(token ? "loading" : "missing"); // loading | ready | error | missing
  const [passError, setPassError] = useState("");
  const { secondsLeft: passLeft, start: startPassTimer } = useCountdown();

  const [usn, setUsn] = useState(rememberedUsn);
  const [usnConfirmed, setUsnConfirmed] = useState(() => Boolean(rememberedUsn()));
  const [liveCamera, setLiveCamera] = useState(cameraAvailable);
  const [cameraNote, setCameraNote] = useState("");
  const [scanKey, setScanKey] = useState(0);
  const [scanOutcome, setScanOutcome] = useState(null);

  // manual (fallback) flow
  const [image, setImage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  // Trade the scanned QR token for a check-in pass, then hide the token.
  useEffect(() => {
    if (!sessionId || !token) return undefined;

    let cancelled = false;

    (async () => {
      try {
        const response = await axios.post(`${API_URL}/attendance/pass`, {
          session_id: sessionId,
          token,
          device: getStoredDevice(),
        });
        if (cancelled) return;
        rememberDevice(response.data.device);
        setPass(response.data.pass);
        startPassTimer(response.data.expires_in);
        setPassStatus("ready");
        navigate(`/mark?session=${encodeURIComponent(sessionId)}`, { replace: true });
      } catch (err) {
        if (cancelled) return;
        setPassError(
          err.response?.data?.message || "Could not start check-in. Scan the QR code again."
        );
        setPassStatus("error");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [sessionId, token, navigate, startPassTimer]);

  // One check-in request: the server verifies the face and records attendance
  // together, so attendance can't be marked without a face match.
  const submitCheckIn = (file, captureMode) => {
    const formData = new FormData();
    formData.append("session_id", sessionId);
    formData.append("usn", usn.trim());
    formData.append("image", file);
    formData.append("pass", pass);
    formData.append("capture_mode", captureMode);
    formData.append("device", getStoredDevice());

    return axios.post(`${API_URL}/attendance/mark`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  };

  // Hands-free scan: translate the server's answer for the scanner.
  const scanAttempt = async (file) => {
    try {
      const response = await submitCheckIn(file, "live");
      rememberUsn(usn.trim());
      return { type: "success", name: response.data.name, held: Boolean(response.data.held) };
    } catch (err) {
      const status = err.response?.status;
      const data = err.response?.data;

      if (status === 403 && data?.verified === false) return { type: "mismatch" };
      if (status === 422) {
        // No face / unreadable frame / a picture of a screen: keep scanning
        // with a friendlier hint than the server's "photo" wording.
        const spoof = /screen|print/i.test(data?.message || "");
        return {
          type: "noface",
          message: spoof
            ? "Look at the camera directly. Pictures of screens or prints aren't accepted."
            : "Move your face into the circle",
        };
      }
      return {
        type: "fatal",
        message: data?.message || "Cannot reach the server. Check your connection and try again.",
      };
    }
  };

  const markAttendance = async (e) => {
    e.preventDefault();
    setResult(null);

    try {
      setLoading(true);
      const response = await submitCheckIn(image, "file");
      rememberUsn(usn.trim());
      setResult({
        type: response.data.held ? "warning" : "success",
        title: response.data.held ? "Sent for review" : "Attendance recorded",
        text: response.data.message,
        held: Boolean(response.data.held),
      });
    } catch (err) {
      console.error(err);
      if (err.response?.data?.verified === false) {
        setResult({
          type: "error",
          title: "Face verification failed",
          text: "Your selfie did not match the registered face. Try again in good lighting.",
        });
      } else {
        setResult({
          type: "error",
          title: "Could not mark attendance",
          text: err.response?.data?.message || "Something went wrong. Please try again.",
        });
      }
    } finally {
      setLoading(false);
    }
  };

  const retryScan = () => {
    setScanOutcome(null);
    setScanKey((key) => key + 1);
  };

  const changeUsn = () => {
    setScanOutcome(null);
    setUsnConfirmed(false);
  };

  const done = result?.type === "success" || Boolean(result?.held);
  const passExpired = passStatus === "ready" && passLeft === 0;
  const needsRescan = (text) => /scan the qr/i.test(text || "");

  const scanButton = (
    <Link to="/scan" className="ft-btn ft-btn--primary ft-btn--block">
      <ScanIcon size={18} /> Scan session QR
    </Link>
  );

  const windowNote = (
    <p className="ft-hint ft-hint--top" role="timer">
      Check-in window: <strong>{formatCountdown(passLeft)}</strong> left
    </p>
  );

  const scanOutcomePanel = () => {
    if (scanOutcome.type === "success") {
      return (
        <div
          className={`ft-scan-result ft-scan-result--${scanOutcome.held ? "warn" : "success"}`}
          role="status"
        >
          <CheckIcon size={48} />
          <strong>{scanOutcome.held ? "Sent for review" : "Checked in"}</strong>
          <span>{scanOutcome.name || usn}</span>
          {scanOutcome.held && (
            <span>Your face matched, but not clearly. Your faculty will confirm your attendance.</span>
          )}
        </div>
      );
    }

    const copy = {
      mismatch: {
        tone: "bad",
        title: "Face not recognised",
        text: `We couldn't match your face to ${usn}. Check that the USN is yours and try again in good light.`,
      },
      timeout: {
        tone: "warn",
        title: "Couldn't see your face",
        text: "Hold the phone at eye level, in good light, and try again.",
      },
      fatal: { tone: "bad", title: "Check-in stopped", text: scanOutcome.message },
    }[scanOutcome.type];

    return (
      <>
        <div className={`ft-scan-result ft-scan-result--${copy.tone}`} role="alert">
          <strong>{copy.title}</strong>
          <span>{copy.text}</span>
        </div>
        {needsRescan(copy.text) && scanButton}
        {!needsRescan(copy.text) && (
          <button type="button" className="ft-btn ft-btn--primary ft-btn--block" onClick={retryScan}>
            Try again
          </button>
        )}
        <p className="ft-hint">
          Wrong USN?{" "}
          <button type="button" className="ft-linkbtn" onClick={changeUsn}>Change it</button>
        </p>
      </>
    );
  };

  let body;

  if (!sessionId) {
    body = (
      <>
        <Notice type="warning" title="No active session">
          Scan the QR code displayed by your faculty to open a check-in link.
        </Notice>
        {scanButton}
      </>
    );
  } else if (passStatus === "loading") {
    body = (
      <p className="ft-hint" role="status">
        <span className="ft-spinner ft-spinner--dark" /> Verifying the QR code…
      </p>
    );
  } else if (passStatus === "error") {
    body = (
      <>
        <Notice type="error" title="Check-in link not accepted">{passError}</Notice>
        {scanButton}
      </>
    );
  } else if (passStatus === "missing" || (passExpired && scanOutcome?.type !== "success")) {
    body = (
      <>
        <Notice type="warning" title={passExpired ? "Check-in window ended" : "Scan the QR code to check in"}>
          {passExpired
            ? "Your check-in window ran out. Scan the QR code shown by your faculty again."
            : "The QR code refreshes every few seconds, so old screenshots and saved links don't work. Scan the one on your faculty's screen."}
        </Notice>
        {scanButton}
      </>
    );
  } else if (liveCamera) {
    // Hands-free: enter the USN once, then just look at the phone.
    body = (
      <>
        {!scanOutcome && windowNote}

        {!usnConfirmed ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (usn.trim()) setUsnConfirmed(true);
            }}
          >
            <div className="ft-field">
              <label className="ft-label" htmlFor="usn">University Seat Number (USN)</label>
              <input
                id="usn"
                className="ft-input"
                type="text"
                placeholder="e.g. 1RV21CS001"
                autoComplete="off"
                value={usn}
                onChange={(e) => setUsn(e.target.value.toUpperCase())}
              />
              <p className="ft-hint">You only enter this once on this phone. Then the camera scans your face by itself.</p>
            </div>
            <button className="ft-btn ft-btn--primary ft-btn--block" type="submit" disabled={!usn.trim()}>
              Continue
            </button>
          </form>
        ) : scanOutcome ? (
          scanOutcomePanel()
        ) : (
          <>
            <p className="ft-scan-as">
              Checking in as <strong>{usn}</strong> ·{" "}
              <button type="button" className="ft-linkbtn" onClick={changeUsn}>Not you?</button>
            </p>
            <FaceScan
              key={scanKey}
              onAttempt={scanAttempt}
              onFinish={setScanOutcome}
              onUnavailable={(message) => {
                setLiveCamera(false);
                setCameraNote(message);
              }}
            />
          </>
        )}
      </>
    );
  } else {
    // Fallback: no camera access in the page, so use the phone's camera app.
    body = (
      <form onSubmit={markAttendance}>
        {result && <Notice type={result.type} title={result.title}>{result.text}</Notice>}

        {windowNote}

        <div className="ft-field">
          <label className="ft-label" htmlFor="usn">University Seat Number (USN)</label>
          <input
            id="usn"
            className="ft-input"
            type="text"
            placeholder="e.g. 1RV21CS001"
            value={usn}
            disabled={done}
            onChange={(e) => setUsn(e.target.value.toUpperCase())}
          />
        </div>

        <div className="ft-field">
          <span className="ft-label">Verification selfie</span>
          <FileDrop
            id="selfie-file"
            file={image}
            onChange={setImage}
            capture="user"
            title="Take a selfie"
            hint="Opens your camera on mobile devices"
          />
          <p className="ft-hint">
            {cameraNote || "Live camera scanning needs a secure (https) connection, so your phone's camera app is used."}
          </p>
        </div>

        {result && needsRescan(result.text) && scanButton}

        <button
          className="ft-btn ft-btn--primary ft-btn--block"
          type="submit"
          disabled={loading || done || !usn.trim() || !image}
        >
          {loading && <span className="ft-spinner" />}
          {loading ? "Verifying face…" : done ? "Attendance marked" : "Verify & mark attendance"}
        </button>
      </form>
    );
  }

  return (
    <PageShell
      width="md"
      icon={ScanIcon}
      title="Mark Attendance"
      subtitle="Verify your face to check in to this class session"
      footer={
        <>
          <Link to="/student-portal" className="ft-link">Student Portal</Link>
          <Link to="/attendance" className="ft-link">View my attendance</Link>
        </>
      }
    >
      {body}
    </PageShell>
  );
}

// A different session always starts from a clean slate (no leftover result,
// pass or scan state from the previous one).
function MarkAttendance() {
  const [params] = useSearchParams();
  return <MarkAttendanceInner key={params.get("session") || ""} />;
}

export default MarkAttendance;
