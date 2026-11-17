import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import axios from "axios";
import { API_URL } from "../config";
import PageShell from "../components/PageShell";
import Notice from "../components/Notice";
import FileDrop from "../components/FileDrop";
import FaceScan from "../components/FaceScan";
import { cameraAvailable } from "../lib/camera";
import { ensureDevice } from "../lib/deviceId";
import { CameraIcon, CheckIcon, LockIcon, ScanIcon } from "../components/Icons";

/**
 * Face registration is not part of the Student Portal menu. It opens from the
 * enrollment QR / link faculty show during an enrollment window
 * (/face-register?code=XXXXXX). Without a code only a short explainer and a
 * code box are shown, never the photo upload.
 */
function RegistrationClosed() {
  const navigate = useNavigate();
  const [code, setCode] = useState("");

  const submit = (e) => {
    e.preventDefault();
    const clean = code.trim().toUpperCase();
    if (clean) navigate(`/face-register?code=${encodeURIComponent(clean)}`);
  };

  return (
    <PageShell
      width="md"
      icon={LockIcon}
      title="Face Registration"
      subtitle="Opens only while your faculty runs an enrollment"
      back={{ to: "/student-portal", label: "Student Portal" }}
      footer={
        <>
          <Link to="/face-status" className="ft-link">Check face status</Link>
          <span />
        </>
      }
    >
      <Notice type="info" title="Registration is locked">
        Your faculty opens a short enrollment window in class and shows a QR code and a
        6-character code. Scan the QR, or enter the code below.
      </Notice>

      <Link to="/scan" className="ft-btn ft-btn--primary ft-btn--block">
        <ScanIcon size={18} /> Scan enrollment QR
      </Link>

      <div className="ft-divider"><span>or enter the code</span></div>

      <form onSubmit={submit}>
        <label className="ft-label" htmlFor="enrollment-code-entry">Enrollment code</label>
        <div className="ft-row">
          <input
            id="enrollment-code-entry"
            className="ft-input"
            type="text"
            placeholder="6-character code"
            autoComplete="off"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
          />
          <button className="ft-btn ft-btn--primary" type="submit" disabled={code.trim().length < 4}>
            Continue
          </button>
        </div>
      </form>
    </PageShell>
  );
}

const SCAN_MS = 45000; // a little longer than a check-in: quality is checked

function RegistrationForm({ initialCode }) {
  const [usn, setUsn] = useState("");
  const [code, setCode] = useState(initialCode);
  const [image, setImage] = useState(null);
  const [liveCamera, setLiveCamera] = useState(cameraAvailable);
  const [cameraNote, setCameraNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  // hands-free scan
  const [scanning, setScanning] = useState(false);
  const [device, setDevice] = useState("");
  const [outcome, setOutcome] = useState(null);

  const send = (files, captureMode, deviceToken) => {
    const formData = new FormData();
    formData.append("usn", usn.trim());
    formData.append("enrollment_code", code.trim());
    formData.append("device", deviceToken);
    formData.append("capture_mode", captureMode);
    formData.append("image", files[0]);
    if (files[1]) formData.append("image2", files[1]);

    return axios.post(`${API_URL}/face/register`, formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  };

  // Camera-app fallback (no live camera in the page): one photo, one button.
  const registerFace = async (e) => {
    e.preventDefault();
    setResult(null);

    try {
      setLoading(true);
      const response = await send([image], "file", await ensureDevice());
      setResult({ type: "success", text: response.data.message || "Face submitted. Your faculty will approve it." });
    } catch (err) {
      console.error(err);
      setResult({
        type: "error",
        text: err.response?.data?.message || "Face registration failed. Please try again.",
      });
    } finally {
      setLoading(false);
    }
  };

  // Hands-free: the page sends frames by itself until one passes the server's
  // quality checks, so there is no capture button.
  const startScan = async (e) => {
    e.preventDefault();
    setResult(null);
    setOutcome(null);

    try {
      setLoading(true);
      setDevice(await ensureDevice());
      setScanning(true);
    } catch (err) {
      console.error(err);
      setResult({ type: "error", text: "Cannot reach the server. Check your connection and try again." });
    } finally {
      setLoading(false);
    }
  };

  const scanAttempt = async (files) => {
    try {
      const response = await send(files, "live", device);
      return { type: "success", message: response.data.message };
    } catch (err) {
      const data = err.response?.data;

      // Unclear frame (too far, dark, blurry...): keep scanning and say what to fix.
      if (err.response?.status === 422) {
        return { type: "noface", message: data?.message || "Move your face into the circle" };
      }

      return {
        type: "fatal",
        message: data?.message || "Cannot reach the server. Check your connection and try again.",
      };
    }
  };

  const finishScan = (finished) => {
    setScanning(false);
    setOutcome(finished);
  };

  const tryAgain = () => {
    setOutcome(null);
    setResult(null);
  };

  let photoStep;

  if (!liveCamera) {
    photoStep = (
      <>
        <div className="ft-field">
          <span className="ft-label">Your photo (taken now, face the camera)</span>
          <FileDrop
            id="face-image"
            file={image}
            onChange={setImage}
            capture="user"
            title="Take a profile photo"
            hint={cameraNote || "Opens your camera on mobile devices"}
          />
          <p className="ft-hint">Use good lighting and keep your face unobstructed.</p>
        </div>

        <button
          className="ft-btn ft-btn--primary ft-btn--block"
          type="submit"
          disabled={loading || !usn.trim() || !code.trim() || !image}
        >
          {loading && <span className="ft-spinner" />}
          {loading ? "Registering…" : "Register face"}
        </button>
      </>
    );
  }

  let body;

  if (liveCamera && outcome?.type === "success") {
    body = (
      <>
        <div className="ft-scan-result ft-scan-result--success" role="status">
          <CheckIcon size={48} />
          <strong>Face registered</strong>
          <span>{usn}</span>
        </div>
        <Notice type="info" title="Waiting for approval">
          Your faculty will check the photo and approve it. After that you can check in by scanning the session QR.
        </Notice>
      </>
    );
  } else if (liveCamera && scanning) {
    body = (
      <>
        <p className="ft-scan-as">
          Registering <strong>{usn}</strong>
        </p>
        <FaceScan
          frames={2}
          maxMs={SCAN_MS}
          onAttempt={scanAttempt}
          onFinish={finishScan}
          onUnavailable={(message) => {
            setScanning(false);
            setLiveCamera(false);
            setCameraNote(message);
          }}
        />
        <p className="ft-hint">Look straight at the phone, in good light, with only you in view. It captures by itself.</p>
      </>
    );
  } else if (liveCamera) {
    const failed = outcome
      ? {
          title: outcome.type === "timeout" ? "Couldn't get a clear photo" : "Registration stopped",
          text:
            outcome.type === "timeout"
              ? "Hold the phone at eye level, in good light, with only your face in view, then try again."
              : outcome.message,
        }
      : null;

    body = (
      <form onSubmit={startScan}>
        {failed && <Notice type="error" title={failed.title}>{failed.text}</Notice>}
        {result && <Notice type={result.type}>{result.text}</Notice>}

        <div className="ft-field">
          <label className="ft-label" htmlFor="usn">University Seat Number (USN)</label>
          <input
            id="usn"
            className="ft-input"
            type="text"
            placeholder="e.g. 1RV21CS001"
            value={usn}
            onChange={(e) => setUsn(e.target.value.toUpperCase())}
          />
        </div>

        <div className="ft-field">
          <label className="ft-label" htmlFor="enrollment-code">Enrollment code</label>
          <input
            id="enrollment-code"
            className="ft-input"
            type="text"
            placeholder="6-character code shown by your faculty"
            autoComplete="off"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
          />
          <p className="ft-hint">Codes expire after a few minutes. Ask your faculty for a new one if it stops working.</p>
        </div>

        <button
          className="ft-btn ft-btn--primary ft-btn--block"
          type="submit"
          disabled={loading || !usn.trim() || !code.trim()}
          onClick={failed ? tryAgain : undefined}
        >
          {loading && <span className="ft-spinner" />}
          {failed ? "Try again" : "Start face scan"}
        </button>
        <p className="ft-hint">The camera then captures your face by itself. There is no photo button.</p>
      </form>
    );
  } else {
    body = (
      <form onSubmit={registerFace}>
        {result && <Notice type={result.type}>{result.text}</Notice>}

        <div className="ft-field">
          <label className="ft-label" htmlFor="usn">University Seat Number (USN)</label>
          <input
            id="usn"
            className="ft-input"
            type="text"
            placeholder="e.g. 1RV21CS001"
            value={usn}
            onChange={(e) => setUsn(e.target.value.toUpperCase())}
          />
        </div>

        <div className="ft-field">
          <label className="ft-label" htmlFor="enrollment-code">Enrollment code</label>
          <input
            id="enrollment-code"
            className="ft-input"
            type="text"
            placeholder="6-character code shown by your faculty"
            autoComplete="off"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
          />
          <p className="ft-hint">Codes expire after a few minutes. Ask your faculty for a new one if it stops working.</p>
        </div>

        {photoStep}
      </form>
    );
  }

  return (
    <PageShell
      width="md"
      icon={CameraIcon}
      title="Face Registration"
      subtitle="Enrol your face once, in class, to use QR check-ins"
      back={{ to: "/student-portal", label: "Student Portal" }}
      footer={
        <>
          <Link to="/face-status" className="ft-link">Check face status</Link>
        </>
      }
    >
      {body}
    </PageShell>
  );
}

function FaceRegistration() {
  const [params] = useSearchParams();
  const code = (params.get("code") || "").trim().toUpperCase();

  // key: a new code in the URL starts a fresh form.
  return code ? <RegistrationForm key={code} initialCode={code} /> : <RegistrationClosed />;
}

export default FaceRegistration;
