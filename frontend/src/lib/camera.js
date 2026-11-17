// The in-page camera needs a secure context (https, or localhost) and a
// browser that exposes getUserMedia. Over plain http on a LAN address neither
// is true, and the page falls back to the phone's camera app.
export const cameraAvailable = () =>
  typeof window !== "undefined" &&
  window.isSecureContext === true &&
  Boolean(navigator.mediaDevices?.getUserMedia);
