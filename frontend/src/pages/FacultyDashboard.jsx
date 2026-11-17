import { useState, useEffect, useRef } from "react";
import { QRCodeCanvas } from "qrcode.react";
import { useNavigate } from "react-router-dom";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { FRONTEND_URL } from "../config";
import {
  facultyApi,
  clearFacultySession,
  FACULTY_KEY,
  TOKEN_KEY,
} from "../lib/facultyApi";
import useCountdown, { formatCountdown, timerTone } from "../hooks/useCountdown";
import Notice from "../components/Notice";
import EnrollmentPanel from "../components/EnrollmentPanel";
import SelfieCell from "../components/SelfieCell";
import LiveCheckIns from "../components/LiveCheckIns";
import { CapIcon } from "../components/Icons";
import "../components/ui.css";
import "./FacultyDashboard.css";

const LOCAL_HOSTS = ["localhost", "127.0.0.1", "[::1]"];

// The backend reports its LAN address so phones on the same Wi-Fi can open the
// QR link during local development. On a deployed site the page origin is
// already the correct public URL, so only override it when running locally.
const isLocalOrigin = (url) => {
  try {
    return LOCAL_HOSTS.includes(new URL(url).hostname);
  } catch {
    return false;
  }
};

// Backend timestamps are UTC, sometimes without a zone suffix and with
// microsecond precision. Normalise so every browser parses them the same way.
const parseUtc = (value) => {
  if (!value) return null;
  let s = String(value).trim().replace(" ", "T").replace(/(\.\d{3})\d+/, "$1");
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(s)) s += "Z";
  const date = new Date(s);
  return Number.isNaN(date.getTime()) ? null : date;
};

const formatDateTime = (value) => {
  const date = parseUtc(value);
  if (!date) return value || "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
  });
};

const formatTick = (value) => {
  const date = parseUtc(value);
  if (!date) return value;
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
};

const assignmentKey = (a) => `${a.department_code}-${a.class_name}-${a.subject_code}`;

// Used only if an older backend doesn't report expires_in on session create.
const DEFAULT_SESSION_SECONDS = 120;
const HISTORY_PREVIEW = 10;
const DEFAULT_ENROLLMENT_SECONDS = 600;

// navigator.clipboard only exists in secure contexts (https / localhost), but
// the QR link is usually opened over plain http on a LAN address.
const copyText = async (text) => {
  try {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.appendChild(field);
    field.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(field);
    return ok;
  } catch {
    return false;
  }
};

function FacultyDashboard() {
  const navigate = useNavigate();

  const [facultyId] = useState(() => localStorage.getItem(FACULTY_KEY) || "");
  const [signedIn] = useState(() => Boolean(localStorage.getItem(TOKEN_KEY)));
  const [assignments, setAssignments] = useState([]);
  const [sessionId, setSessionId] = useState("");
  const [sessionAssignment, setSessionAssignment] = useState(null);
  const [attendanceSummary, setAttendanceSummary] = useState(null);
  const [sessionHistory, setSessionHistory] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [trendData, setTrendData] = useState([]);
  const [selectedAssignment, setSelectedAssignment] = useState(null);
  // null = not requested yet, [] = requested and nobody is below the threshold
  const [defaulters, setDefaulters] = useState(null);
  const [qrFrontendUrl, setQrFrontendUrl] = useState(FRONTEND_URL);
  const [copied, setCopied] = useState(false);
  const [creatingKey, setCreatingKey] = useState("");
  const [notice, setNotice] = useState(null);
  const [showAllHistory, setShowAllHistory] = useState(false);
  // Live (text-only) view of the open session, refreshed every few seconds.
  const [liveData, setLiveData] = useState(null);
  const [busyUsn, setBusyUsn] = useState("");
  // Testing only: let students check in from any network for new sessions.
  const [allowAnyNetwork, setAllowAnyNetwork] = useState(false);
  const [sessionTotal, setSessionTotal] = useState(DEFAULT_SESSION_SECONDS);
  const [enrollment, setEnrollment] = useState(null);
  const enrollTimer = useCountdown();
  // The QR carries a token that rotates every few seconds (so a shared
  // screenshot goes stale); qrTimer counts down to the next refresh.
  const [qrToken, setQrToken] = useState("");
  const [qrRotate, setQrRotate] = useState(15);
  const sessionIdRef = useRef("");
  const sessionActiveRef = useRef(false);
  const fetchQrRef = useRef(null);
  const qrTimer = useCountdown(() => {
    if (sessionActiveRef.current) fetchQrRef.current?.(sessionIdRef.current);
  });
  // When the session ends, refresh history so its row flips from Active to Expired.
  const sessionTimer = useCountdown(() => {
    sessionActiveRef.current = false;
    facultyApi
      .get(`/session/history/${facultyId}`)
      .then((response) => setSessionHistory(response.data))
      .catch(() => {});
  });
  const secondsLeft = sessionTimer.secondsLeft;
  const copyTimer = useRef(null);

  const checkInUrl = qrToken ? `${qrFrontendUrl}/mark?session=${sessionId}&t=${qrToken}` : "";

  const fail = (text) => setNotice({ type: "error", text });

  useEffect(() => {
    if (!facultyId || !signedIn) {
      clearFacultySession();
      navigate("/", { replace: true });
      return undefined;
    }

    let cancelled = false;

    const loadInitialData = async () => {
      const [assignmentsRes, historyRes, serverRes] = await Promise.allSettled([
        facultyApi.get(`/assignment/faculty/${facultyId}`),
        facultyApi.get(`/session/history/${facultyId}`),
        isLocalOrigin(FRONTEND_URL) ? facultyApi.get(`/server/info`) : Promise.resolve(null),
      ]);
      if (cancelled) return;

      if (assignmentsRes.status === "fulfilled") setAssignments(assignmentsRes.value.data);
      if (historyRes.status === "fulfilled") setSessionHistory(historyRes.value.data);
      if (assignmentsRes.status === "rejected" && historyRes.status === "rejected") {
        setNotice({
          type: "error",
          text: "Could not load your dashboard. Check that the backend is running.",
        });
      }
      if (serverRes.status === "fulfilled") {
        const info = serverRes.value?.data;
        if (info?.lan_ip) {
          // Same scheme and port as this page (https in https dev mode), so the
          // QR opens the right address on a phone.
          const port = window.location.port ? `:${window.location.port}` : "";
          setQrFrontendUrl(`${window.location.protocol}//${info.lan_ip}${port}`);
        } else if (info?.frontend_url) {
          setQrFrontendUrl(info.frontend_url);
        }
      }
    };

    loadInitialData();
    return () => {
      cancelled = true;
    };
  }, [facultyId, signedIn, navigate]);

  useEffect(() => () => clearTimeout(copyTimer.current), []);

  const fetchQr = async (sid) => {
    if (!sid) return;
    try {
      const response = await facultyApi.get(`/session/${sid}/qr`);
      if (sid !== sessionIdRef.current) return; // a newer session took over
      setQrToken(response.data.token);
      setQrRotate(response.data.rotate_seconds || 15);
      qrTimer.start(response.data.expires_in);
    } catch (error) {
      if (error.response?.status === 410) {
        sessionActiveRef.current = false; // session ended
        return;
      }
      // Keep showing the last token (still valid for a moment) and retry soon.
      if (sid === sessionIdRef.current && sessionActiveRef.current) qrTimer.start(3);
    }
  };

  useEffect(() => {
    fetchQrRef.current = fetchQr;
  });

  // Browsers slow timers in background tabs; refresh the QR as soon as the
  // dashboard is visible again.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible" && sessionActiveRef.current) {
        fetchQrRef.current?.(sessionIdRef.current);
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  const sessionEnded = Boolean(sessionId) && secondsLeft === 0;

  // Poll a small text-only snapshot while the session is open (and once more
  // when it ends). It pauses while the tab is hidden and never loads images.
  useEffect(() => {
    if (!sessionId) return undefined;

    let cancelled = false;
    let inFlight = false;

    const pull = async () => {
      if (inFlight || document.hidden) return;
      inFlight = true;
      try {
        const response = await facultyApi.get(`/attendance/live/${sessionId}`);
        if (!cancelled) setLiveData(response.data);
      } catch {
        /* keep showing the last snapshot */
      } finally {
        inFlight = false;
      }
    };

    pull();
    if (sessionEnded) return () => { cancelled = true; };

    const timer = setInterval(pull, 4000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [sessionId, sessionEnded]);

  const refreshLive = async () => {
    try {
      const response = await facultyApi.get(`/attendance/live/${sessionId}`);
      setLiveData(response.data);
    } catch {
      /* the next poll will catch up */
    }
  };

  const rejectCheckIn = async (usn, rejected) => {
    setBusyUsn(usn);
    try {
      await facultyApi.post(`/attendance/reject/${sessionId}/${usn}`, { rejected });
      await refreshLive();
      if (attendanceSummary) {
        const sheet = await facultyApi.get(`/attendance/final/${sessionId}`);
        setAttendanceSummary(sheet.data);
      }
    } catch (error) {
      console.error(error);
      fail("Could not update that check-in.");
    } finally {
      setBusyUsn("");
    }
  };

  const allowNetwork = async (network) => {
    try {
      await facultyApi.post(`/session/${sessionId}/network/allow`, { network });
      await refreshLive();
    } catch (error) {
      console.error(error);
      fail(error.response?.data?.message || "Could not allow that network.");
    }
  };

  const refreshHistory = async () => {
    try {
      const response = await facultyApi.get(`/session/history/${facultyId}`);
      setSessionHistory(response.data);
    } catch (error) {
      console.error(error);
    }
  };

  // Analytics + trend (+ optionally the defaulters list) for one assignment.
  const loadInsights = async (assignment, { withDefaulters = false } = {}) => {
    const { subject_code: subject, class_name: className } = assignment;
    const requests = [
      facultyApi.get(`/attendance/analytics/${subject}/${className}`),
      facultyApi.get(`/attendance/trend/${subject}/${className}`),
    ];
    if (withDefaulters) {
      requests.push(facultyApi.get(`/attendance/defaulters/${subject}/${className}`));
    }

    const [analyticsRes, trendRes, defaultersRes] = await Promise.allSettled(requests);
    let failed = false;

    if (analyticsRes.status === "fulfilled") {
      const data = analyticsRes.value.data;
      // The API answers 200 with only a message when the class has no students.
      setAnalytics(data && data.total_students !== undefined ? data : null);
      if (data && data.total_students === undefined && data.message) {
        setNotice({ type: "info", text: `${data.message} for class ${className}.` });
      }
    } else {
      setAnalytics(null);
      failed = true;
    }

    if (trendRes.status === "fulfilled") {
      setTrendData(Array.isArray(trendRes.value.data) ? trendRes.value.data : []);
    } else {
      setTrendData([]);
      failed = true;
    }

    if (withDefaulters) {
      if (defaultersRes.status === "fulfilled") {
        setDefaulters(Array.isArray(defaultersRes.value.data) ? defaultersRes.value.data : []);
      } else {
        setDefaulters(null);
        failed = true;
      }
    }

    if (failed) fail(`Some details for ${subject} could not be loaded.`);
  };

  const createSession = async (assignment) => {
    if (creatingKey) return;
    setNotice(null);
    setCreatingKey(assignmentKey(assignment));
    let newSessionId;

    try {
      const response = await facultyApi.post(`/session/create`, {
        department_code: assignment.department_code,
        class_name: assignment.class_name,
        subject_code: assignment.subject_code,
        allow_any_network: allowAnyNetwork,
      });

      const total = Number(response.data.expires_in) || DEFAULT_SESSION_SECONDS;
      setSessionTotal(total);
      sessionTimer.start(total);

      newSessionId = response.data.session_id;
      sessionIdRef.current = newSessionId;
      sessionActiveRef.current = true;
      setQrToken("");
      setLiveData(null);

      setSessionId(response.data.session_id);
      setSessionAssignment(assignment);
      setSelectedAssignment(assignment);
      setAttendanceSummary(null);
      setDefaulters(null);
    } catch (error) {
      console.error(error);
      fail(error.response?.data?.message || "Failed to create the session. Please try again.");
      setCreatingKey("");
      return;
    }
    setCreatingKey("");

    // The session exists at this point; a failure below must not report otherwise.
    await Promise.all([loadInsights(assignment), refreshHistory(), fetchQr(newSessionId)]);
  };

  const showAnalytics = async (assignment) => {
    setNotice(null);
    setSelectedAssignment(assignment);
    setDefaulters(null);
    await loadInsights(assignment, { withDefaulters: true });
  };

  const viewAttendance = async () => {
    setNotice(null);
    try {
      const response = await facultyApi.get(`/attendance/final/${sessionId}`);
      setAttendanceSummary(response.data);
    } catch (error) {
      console.error(error);
      fail("Failed to fetch attendance for this session.");
    }
  };

  const downloadCSV = async () => {
    try {
      const response = await facultyApi.get(`/attendance/export/${sessionId}`, {
        responseType: "blob",
      });
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = `attendance_${sessionId}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error(error);
      fail("Could not download the CSV.");
    }
  };

  const openEnrollment = async (assignment) => {
    setNotice(null);
    try {
      const response = await facultyApi.post("/enrollment/create", {
        class_name: assignment.class_name,
        allow_any_network: allowAnyNetwork,
      });
      const total = Number(response.data.expires_in) || DEFAULT_ENROLLMENT_SECONDS;
      setEnrollment({
        code: response.data.code,
        className: response.data.class_name,
        total,
        networkLock: response.data.network_lock,
      });
      enrollTimer.start(total);
    } catch (error) {
      console.error(error);
      fail(error.response?.data?.message || "Could not create an enrollment code.");
    }
  };

  const resetFace = async (usn) => {
    try {
      const response = await facultyApi.delete(`/face/delete/${encodeURIComponent(usn)}`);
      return { type: "success", text: `${response.data.message}. ${usn} can now register again.` };
    } catch (error) {
      console.error(error);
      return {
        type: "error",
        text: error.response?.data?.message || "Could not reset this face.",
      };
    }
  };

  const copyLink = async () => {
    if (!checkInUrl) return;
    const ok = await copyText(checkInUrl);
    if (!ok) {
      fail("Could not copy automatically. Select the link and copy it manually.");
      return;
    }
    setCopied(true);
    clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopied(false), 2000);
  };

  const logout = () => {
    clearFacultySession();
    navigate("/");
  };

  const isActive = (status) => String(status).toLowerCase() === "active";

  const sessionExpired = Boolean(sessionId) && secondsLeft === 0;
  const tone = sessionExpired ? "bad" : timerTone(secondsLeft ?? 0);
  const progress = sessionTotal ? Math.min(100, ((secondsLeft ?? 0) / sessionTotal) * 100) : 0;

  const visibleHistory = showAllHistory ? sessionHistory : sessionHistory.slice(0, HISTORY_PREVIEW);

  return (
    <div className="dashboard-container">
      <div className="dashboard-header">
        <div className="header-left">
          <div className="header-icon">
            <CapIcon size={20} stroke="#fff" />
          </div>
          <div>
            <h1 className="dashboard-title">Smart Attendance Dashboard</h1>
            <div className="dashboard-subtitle">Logged in as {facultyId}</div>
          </div>
        </div>
        <div className="header-right">
          <div className="date-chip">
            {new Date().toLocaleDateString(undefined, {
              weekday: "long",
              year: "numeric",
              month: "long",
              day: "numeric",
            })}
          </div>
          <button className="secondary-btn" onClick={logout}>
            Logout
          </button>
        </div>
      </div>

      {notice && <Notice type={notice.type}>{notice.text}</Notice>}

      <div className="dashboard-cards">
        <div className="card accent-indigo">
          <h3>Total Assignments</h3>
          <p>{assignments.length}</p>
        </div>
        <div className="card accent-amber">
          <h3>{sessionExpired ? "Last Session" : "Active Session"}</h3>
          <p className="card-note">{sessionId ? `ID: ${sessionId}` : "No Active Session"}</p>
          {sessionId && (
            <span className={`card-timer card-timer--${tone}`}>
              {sessionExpired ? "Expired" : `${formatCountdown(secondsLeft)} left`}
            </span>
          )}
        </div>
        <div className="card accent-green">
          <h3>Class Average</h3>
          <p>{analytics ? `${analytics.average_attendance}%` : "—"}</p>
        </div>
        <div className="card accent-rose">
          <h3>Defaulters {"(<85%)"}</h3>
          <p>{analytics ? analytics.below_85 : "—"}</p>
        </div>
      </div>

      {sessionId && (
        <div className="section">
          <div className="section-head">
            <h2 className="section-title">Active Session Panel</h2>
          </div>
          <div className="session-panel">
            <div className="qr-card">
              <h3>Attendance Scan QR</h3>
              <p className="qr-desc">Students scan to verify identity and check-in</p>

              <div className={`session-timer session-timer--${tone}`} role="timer">
                <div className="session-timer__row">
                  <span className="session-timer__label">
                    {sessionExpired ? "Session expired" : "Time remaining"}
                  </span>
                  <strong className="session-timer__time">{formatCountdown(secondsLeft)}</strong>
                </div>
                <div className="session-timer__bar">
                  <i style={{ width: `${progress}%` }} />
                </div>
                {sessionExpired && (
                  <p className="session-timer__hint">
                    Check-ins are closed. Generate a new session to continue.
                  </p>
                )}
              </div>

              <div className={`qr-wrapper ${sessionExpired ? "qr-wrapper--expired" : ""}`}>
                {checkInUrl ? (
                  <QRCodeCanvas value={checkInUrl} size={240} level="H" includeMargin={true} />
                ) : (
                  <div className="qr-placeholder">Preparing QR…</div>
                )}
              </div>
              {!sessionExpired && (
                <p className="qr-rotate" role="status">
                  New code in <strong>{qrTimer.secondsLeft ?? "…"}s</strong> · the QR changes every{" "}
                  {qrRotate}s so shared screenshots stop working
                </p>
              )}

              <div className="qr-link">
                <p className="qr-link__label">Direct link (changes every {qrRotate}s):</p>
                <div className="qr-link__row">
                  <a href={checkInUrl || undefined} target="_blank" rel="noreferrer">
                    {checkInUrl || "—"}
                  </a>
                  <button
                    type="button"
                    className={`copy-btn ${copied ? "copy-btn--done" : ""}`}
                    onClick={copyLink}
                  >
                    {copied ? "Copied!" : "Copy"}
                  </button>
                </div>
                <p className="qr-link__note">Note: the phone must be on the same Wi-Fi network.</p>
              </div>

              <div className="session-info-pills">
                <span className="info-pill">Session ID: {sessionId}</span>
                {sessionAssignment && (
                  <span className="info-pill">
                    {sessionAssignment.subject_code} | Class {sessionAssignment.class_name}
                  </span>
                )}
              </div>
              <div className="session-actions">
                <button className="action-btn" onClick={viewAttendance}>
                  View Attendance
                </button>
                <button className="ghost-btn" onClick={downloadCSV}>
                  Download CSV
                </button>
              </div>
            </div>

            <div className="session-side">
            <LiveCheckIns
              sessionId={sessionId}
              data={liveData}
              busyUsn={busyUsn}
              onReject={rejectCheckIn}
              onAllowNetwork={allowNetwork}
              formatTime={(value) =>
                parseUtc(value)?.toLocaleTimeString(undefined, {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                }) ?? "—"
              }
            />

            {attendanceSummary && (
              <div className="attendance-summary-card">
                <h3>Attendance Summary</h3>
                <div className="summary-stats">
                  <div className="stat-pill">
                    <span>Total</span>
                    <strong>{attendanceSummary.total_students}</strong>
                  </div>
                  <div className="stat-pill success">
                    <span>Present</span>
                    <strong>{attendanceSummary.present}</strong>
                  </div>
                  <div className="stat-pill error">
                    <span>Absent</span>
                    <strong>{attendanceSummary.absent}</strong>
                  </div>
                  <div className="stat-pill info">
                    <span>Percentage</span>
                    <strong>{attendanceSummary.attendance_percentage}%</strong>
                  </div>
                </div>

                <div className="table-wrap table-wrap--scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>USN</th>
                        <th>Name</th>
                        <th>Status</th>
                        <th>Time</th>
                        <th>Review</th>
                        <th>Selfie</th>
                      </tr>
                    </thead>
                    <tbody>
                      {attendanceSummary.students.map((student) => {
                        const present = student.status === "Present";
                        return (
                          <tr key={student.usn}>
                            <td>{student.usn}</td>
                            <td>{student.name}</td>
                            <td>
                              <span
                                className={present ? "status-present" : "status-absent"}
                                title={
                                  student.distance != null
                                    ? `Face match distance ${student.distance} (lower is closer)`
                                    : undefined
                                }
                              >
                                {student.status}
                              </span>
                            </td>
                            <td className="nowrap">
                              {student.marked_at
                                ? parseUtc(student.marked_at)?.toLocaleTimeString(undefined, {
                                    hour: "2-digit",
                                    minute: "2-digit",
                                    second: "2-digit",
                                  }) ?? "—"
                                : "—"}
                            </td>
                            <td>
                              {student.flags?.length ? (
                                <span className="flag-list">
                                  {student.flags.map((flag) => (
                                    <span className="flag-badge" key={flag}>{flag}</span>
                                  ))}
                                </span>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td>
                              {student.has_selfie ? (
                                <SelfieCell sessionId={sessionId} usn={student.usn} />
                              ) : (
                                "—"
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            </div>
          </div>
        </div>
      )}

      {enrollment && (
        <EnrollmentPanel
          enrollment={enrollment}
          secondsLeft={enrollTimer.secondsLeft}
          link={`${qrFrontendUrl}/face-register?code=${enrollment.code}`}
          onReset={resetFace}
          onClose={() => setEnrollment(null)}
        />
      )}

      <div className="dashboard-split">
        <div>
          <div className="section-head">
            <h2 className="section-title">Your Assignments</h2>
          </div>
          <label className="network-toggle">
            <input
              type="checkbox"
              checked={allowAnyNetwork}
              onChange={(event) => setAllowAnyNetwork(event.target.checked)}
            />
            Testing only: allow check-ins from any network
          </label>
          <div className="assignment-list">
            {assignments.length === 0 && (
              <div className="empty-state">No assignments found for this faculty ID.</div>
            )}
            {assignments.map((item) => {
              const key = assignmentKey(item);
              const creating = creatingKey === key;
              return (
                <div className="assignment-card" key={key}>
                  <h3>{item.subject_code}</h3>
                  <p>
                    Class: <strong>{item.class_name}</strong>
                  </p>
                  <p>
                    Department: <strong>{item.department_code}</strong>
                  </p>
                  <div className="assignment-buttons">
                    <button
                      className="action-btn"
                      onClick={() => createSession(item)}
                      disabled={Boolean(creatingKey)}
                    >
                      {creating ? "Generating…" : "Generate Session"}
                    </button>
                    <button className="ghost-btn" onClick={() => showAnalytics(item)}>
                      View Analytics
                    </button>
                    <button className="ghost-btn" onClick={() => openEnrollment(item)}>
                      Enrollment Code
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div>
          <div className="section-head">
            <h2 className="section-title">
              Attendance Trend {selectedAssignment && `— ${selectedAssignment.subject_code}`}
            </h2>
          </div>
          {trendData.length > 0 ? (
            <div className="chart-card">
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={trendData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e3e8ef" vertical={false} />
                  <XAxis
                    dataKey="date"
                    tickFormatter={formatTick}
                    minTickGap={24}
                    tick={{ fill: "#4b5a70", fontSize: 11, fontWeight: 500 }}
                    axisLine={{ stroke: "#dfe4ec" }}
                    tickLine={false}
                  />
                  <YAxis
                    domain={[0, 100]}
                    tick={{ fill: "#4b5a70", fontSize: 11, fontWeight: 500 }}
                    axisLine={{ stroke: "#dfe4ec" }}
                    tickLine={false}
                  />
                  <Tooltip
                    labelFormatter={formatDateTime}
                    formatter={(value) => [`${value}%`, "Attendance"]}
                    contentStyle={{
                      backgroundColor: "#ffffff",
                      borderColor: "#dfe4ec",
                      borderRadius: "8px",
                      color: "#0f1b2d",
                      fontFamily: "var(--font-sans)",
                      fontSize: "12px",
                    }}
                  />
                  <Line
                    type="monotone"
                    dataKey="attendance_percentage"
                    stroke="#1f4fa3"
                    strokeWidth={3}
                    activeDot={{ r: 6, fill: "#1f4fa3" }}
                    dot={{ strokeWidth: 2, r: 3 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="chart-card chart-card--empty">
              {selectedAssignment
                ? "No sessions recorded for this subject yet"
                : "Select an assignment to view attendance trends"}
            </div>
          )}
        </div>
      </div>

      {analytics && (
        <div className="section">
          <div className="section-head">
            <h2 className="section-title">Class Analytics — {analytics.subject_code}</h2>
          </div>
          <div className="dashboard-cards">
            <div className="card">
              <h3>Total Students</h3>
              <p>{analytics.total_students}</p>
            </div>
            <div className="card">
              <h3>Average Attendance</h3>
              <p>{analytics.average_attendance}%</p>
            </div>
            <div className="card">
              <h3>Above 85%</h3>
              <p>{analytics.above_85}</p>
            </div>
            <div className="card">
              <h3>Below 85%</h3>
              <p>{analytics.below_85}</p>
            </div>
          </div>
        </div>
      )}

      {defaulters !== null && (
        <div className="section">
          <div className="section-head">
            <h2 className="section-title">Defaulters List — {selectedAssignment?.subject_code}</h2>
          </div>
          {defaulters.length === 0 ? (
            <Notice type="success">No defaulters — every student is at or above 85%.</Notice>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>USN</th>
                    <th>Name</th>
                    <th>Attendance Rate</th>
                  </tr>
                </thead>
                <tbody>
                  {defaulters.map((student) => (
                    <tr key={student.usn}>
                      <td>{student.usn}</td>
                      <td>{student.name}</td>
                      <td>
                        <span className="status-absent">{student.percentage}%</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {sessionHistory.length > 0 && (
        <div className="section">
          <div className="section-head">
            <h2 className="section-title">Session History</h2>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Session ID</th>
                  <th>Department</th>
                  <th>Class</th>
                  <th>Subject</th>
                  <th>Status</th>
                  <th>Created At</th>
                </tr>
              </thead>
              <tbody>
                {visibleHistory.map((session) => (
                  <tr key={session.session_id}>
                    <td className="mono">{session.session_id}</td>
                    <td>{session.department_code}</td>
                    <td>{session.class_name}</td>
                    <td>{session.subject_code}</td>
                    <td>
                      <span className={isActive(session.status) ? "status-active" : "status-expired"}>
                        {session.status}
                      </span>
                    </td>
                    <td className="nowrap">{formatDateTime(session.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {sessionHistory.length > HISTORY_PREVIEW && (
            <div className="history-footer">
              <span>
                Showing {visibleHistory.length} of {sessionHistory.length} sessions
              </span>
              <button
                type="button"
                className="ghost-btn"
                aria-expanded={showAllHistory}
                onClick={() => setShowAllHistory((open) => !open)}
              >
                {showAllHistory ? "Show less" : "View more"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default FacultyDashboard;
