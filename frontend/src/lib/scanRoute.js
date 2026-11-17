// Decide where a scanned QR should send the student. Faculty show two kinds:
//   * session QR     -> <origin>/mark?session=<id>&t=<token>  (check in)
//   * enrollment QR  -> <origin>/face-register?code=<c>        (register a face)
// Only the parameters are used, so a QR pointing at another site can't
// redirect the student there.
export const routeForScan = (decodedText) => {
  const text = String(decodedText ?? "").trim();

  try {
    const url = new URL(text);
    const code = url.searchParams.get("code");
    const session = url.searchParams.get("session");
    const token = url.searchParams.get("t");

    if (url.pathname.endsWith("/face-register") && code) {
      return `/face-register?code=${encodeURIComponent(code)}`;
    }
    if (session) {
      return token
        ? `/mark?session=${encodeURIComponent(session)}&t=${encodeURIComponent(token)}`
        : `/mark?session=${encodeURIComponent(session)}`;
    }
  } catch {
    /* not a URL: treat it as a bare session id below */
  }

  return `/mark?session=${encodeURIComponent(text)}`;
};
