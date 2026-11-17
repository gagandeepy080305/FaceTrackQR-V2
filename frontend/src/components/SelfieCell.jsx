import { useEffect, useState } from "react";
import { facultyApi } from "../lib/facultyApi";

/**
 * A "Photo" button. Nothing is downloaded until it is clicked, and the photo
 * opens in a popup instead of growing the row, so lists stay light.
 */
export default function SelfieCell({ sessionId, usn, label = "Photo", path }) {
  const [src, setSrc] = useState("");
  const [state, setState] = useState("idle"); // idle | loading | error
  const [open, setOpen] = useState(false);

  // Free the image from memory as soon as the popup closes.
  useEffect(() => () => src && URL.revokeObjectURL(src), [src]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => event.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const close = () => {
    setOpen(false);
    setSrc("");
  };

  const show = async () => {
    setState("loading");
    try {
      const response = await facultyApi.get(path || `/attendance/selfie/${sessionId}/${usn}`, {
        responseType: "blob",
      });
      setSrc(URL.createObjectURL(response.data));
      setState("idle");
      setOpen(true);
    } catch {
      setState("error");
    }
  };

  return (
    <>
      <button type="button" className="ghost-btn selfie-btn" onClick={show} disabled={state === "loading"}>
        {state === "loading" ? "Loading…" : state === "error" ? "Retry" : label}
      </button>

      {open && src && (
        <div className="photo-modal" role="dialog" aria-modal="true" aria-label={`Check-in photo of ${usn}`} onClick={close}>
          <div className="photo-modal__card" onClick={(event) => event.stopPropagation()}>
            <img src={src} alt={`Check-in selfie of ${usn}`} />
            <div className="photo-modal__bar">
              <strong>{usn}</strong>
              <button type="button" className="ghost-btn" onClick={close}>Close</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
