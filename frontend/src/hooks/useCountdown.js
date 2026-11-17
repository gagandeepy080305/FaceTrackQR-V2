import { useCallback, useEffect, useRef, useState } from "react";

// Local clock time (ms) at which something lasting `seconds` from now ends.
const endTimeFromNow = (seconds) => Date.now() + seconds * 1000;
const secondsUntil = (endTime) => Math.max(0, Math.ceil((endTime - Date.now()) / 1000));

export const formatCountdown = (seconds) => {
  const safe = Math.max(0, seconds ?? 0);
  const mm = String(Math.floor(safe / 60)).padStart(2, "0");
  const ss = String(safe % 60).padStart(2, "0");
  return `${mm}:${ss}`;
};

// ok > 30s left, warn <= 30s, bad <= 10s or expired
export const timerTone = (seconds) => (seconds <= 10 ? "bad" : seconds <= 30 ? "warn" : "ok");

/**
 * Counts down using absolute time, so a throttled background tab still shows
 * the right value when you return to it. start(seconds) begins a countdown;
 * onExpire (optional) fires once when it reaches zero.
 */
export default function useCountdown(onExpire) {
  const [endsAt, setEndsAt] = useState(null);
  const [secondsLeft, setSecondsLeft] = useState(null);
  const onExpireRef = useRef(onExpire);

  useEffect(() => {
    onExpireRef.current = onExpire;
  });

  useEffect(() => {
    if (!endsAt) return undefined;

    const timer = setInterval(() => {
      const left = secondsUntil(endsAt);
      setSecondsLeft(left);

      if (left === 0) {
        clearInterval(timer);
        onExpireRef.current?.();
      }
    }, 500);

    return () => clearInterval(timer);
  }, [endsAt]);

  const start = useCallback((seconds) => {
    setSecondsLeft(seconds);
    setEndsAt(endTimeFromNow(seconds));
  }, []);

  return { secondsLeft, start };
}
