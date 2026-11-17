import { useEffect, useRef, useState } from "react";
import Webcam from "react-webcam";

const FIRST_ATTEMPT_DELAY_MS = 1200; // let the camera settle on your face
const ATTEMPT_INTERVAL_MS = 1500;
const DEFAULT_MAX_SCAN_MS = 30000;
const FRAME_GAP_MS = 800; // spacing between the frames of one attempt
const MAX_MISMATCHES = 3;
const MAX_SIDE = 960; // downscale frames before upload

const nowMs = () => Date.now();

// Grab the current video frame as a JPEG file (not mirrored, as the model expects).
const captureFrame = (video) =>
  new Promise((resolve) => {
    const scale = Math.min(1, MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(
      (blob) => resolve(blob ? new File([blob], "scan.jpg", { type: "image/jpeg" }) : null),
      "image/jpeg",
      0.9
    );
  });

/**
 * Hands-free face scan: opens the camera, watches for your face and submits
 * frames by itself until you are recognised. There is no capture button and no
 * photo preview.
 *
 * frames: how many frames to send per attempt. 1 (check-in) passes a file to
 * onAttempt; more (registration) passes an array of files taken about a second
 * apart, so the server can confirm they show the same, still person.
 * maxMs: how long to keep trying before giving up.
 *
 * onAttempt(file | files) -> Promise<{ type: "success" | "mismatch" | "noface" | "fatal", ... }>
 * onFinish(outcome) is called once with { type: "success" | "mismatch" | "timeout" | "fatal", ... }
 * onUnavailable(message) is called if the camera can't be used at all.
 */
export default function FaceScan({ onAttempt, onFinish, onUnavailable, frames = 1, maxMs = DEFAULT_MAX_SCAN_MS }) {
  const webcamRef = useRef(null);
  const attemptRef = useRef(onAttempt);
  const finishRef = useRef(onFinish);
  const [ready, setReady] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [hint, setHint] = useState("");

  useEffect(() => {
    attemptRef.current = onAttempt;
    finishRef.current = onFinish;
  });

  useEffect(() => {
    if (!ready) return undefined;

    let cancelled = false;
    let timer;
    let mismatches = 0;
    const deadline = nowMs() + maxMs;

    // Where the browser can find faces on its own (Chrome), only send frames
    // that actually contain one. Elsewhere frames are sent on a timer and the
    // server answers "no face" cheaply.
    let detector = null;
    try {
      if ("FaceDetector" in window) detector = new window.FaceDetector({ fastMode: true, maxDetectedFaces: 1 });
    } catch {
      detector = null;
    }

    const schedule = () => {
      timer = setTimeout(tick, ATTEMPT_INTERVAL_MS);
    };

    const tick = async () => {
      if (cancelled) return;

      if (nowMs() > deadline) {
        finishRef.current({ type: "timeout" });
        return;
      }

      const video = webcamRef.current?.video;
      if (!video || video.readyState < 2 || !video.videoWidth) return schedule();

      if (detector) {
        try {
          const faces = await detector.detect(video);
          if (cancelled) return;
          if (!faces.length) {
            setHint("Look at the camera");
            return schedule();
          }
          if (faces[0].boundingBox.width < video.videoWidth * 0.2) {
            setHint("Move a little closer");
            return schedule();
          }
        } catch {
          detector = null; // unsupported after all: fall back to the timer
        }
      }

      const shots = [];
      for (let i = 0; i < frames; i += 1) {
        if (i > 0) {
          await new Promise((resolve) => setTimeout(resolve, FRAME_GAP_MS));
          if (cancelled) return;
        }
        const shot = await captureFrame(video);
        if (cancelled) return;
        if (!shot) return schedule();
        shots.push(shot);
      }

      setVerifying(true);
      setHint("Checking…");
      const outcome = await attemptRef.current(frames === 1 ? shots[0] : shots);
      if (cancelled) return;
      setVerifying(false);

      if (outcome.type === "success" || outcome.type === "fatal") {
        finishRef.current(outcome);
        return;
      }

      if (outcome.type === "mismatch") {
        mismatches += 1;
        if (mismatches >= MAX_MISMATCHES) {
          finishRef.current({ type: "mismatch" });
          return;
        }
        setHint("Hold still and face the camera");
      } else {
        setHint(outcome.message || "Move your face into the circle");
      }

      schedule();
    };

    timer = setTimeout(tick, FIRST_ATTEMPT_DELAY_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [ready, frames, maxMs]);

  return (
    <div className={`ft-scan ${verifying ? "ft-scan--verifying" : ""}`}>
      <div className="ft-scan__stage">
        <Webcam
          ref={webcamRef}
          audio={false}
          className="ft-scan__video"
          videoConstraints={{ facingMode: "user", width: { ideal: 1280 }, height: { ideal: 1280 } }}
          onUserMedia={() => setReady(true)}
          onUserMediaError={() =>
            onUnavailable?.("Camera access was blocked. Using your phone's camera app instead.")
          }
        />
        <span className="ft-scan__ring" />
      </div>
      <p className="ft-scan__status" role="status">
        {!ready ? "Starting camera…" : hint || "Look at the camera"}
      </p>
    </div>
  );
}
