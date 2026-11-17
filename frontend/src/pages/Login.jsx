import { useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import axios from "axios";
import { API_URL } from "../config";
import { FACULTY_KEY, TOKEN_KEY } from "../lib/facultyApi";
import PageShell from "../components/PageShell";
import Notice from "../components/Notice";
import { ChevronRightIcon } from "../components/Icons";

function Login() {
  const [facultyId, setFacultyId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const sessionExpired = params.get("expired") === "1";

  const facultyLogin = async (e) => {
    e.preventDefault();
    setError("");

    if (!facultyId.trim() || !password) {
      setError("Enter your Faculty ID and password.");
      return;
    }

    try {
      setLoading(true);
      const response = await axios.post(`${API_URL}/faculty/login`, {
        faculty_id: facultyId.trim(),
        password,
      });
      if (!response.data.token) {
        setError("The server did not return a login token. Update the backend and try again.");
        return;
      }
      localStorage.setItem(FACULTY_KEY, response.data.faculty_id);
      localStorage.setItem(TOKEN_KEY, response.data.token);
      navigate("/faculty");
    } catch (err) {
      console.error(err);
      setError(
        err.response
          ? err.response.data?.message || "Sign in failed. Please try again."
          : "Cannot reach the server. Check that the backend is running."
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <PageShell bare>
      <div className="ft-login">
        <aside className="ft-login__aside">
          <h2>Attendance that verifies itself.</h2>
          <p>
            Faculty open a class session and share a QR code. Students check in
            with a face match, so every record is tied to a real person.
          </p>
          <ul className="ft-login__list">
            <li>Time-boxed QR sessions per class</li>
            <li>Face verification at check-in</li>
            <li>Subject-wise reports and defaulter tracking</li>
          </ul>
        </aside>

        <section className="ft-login__form">
          <h1>Faculty sign in</h1>
          <p className="ft-sub">Use your institution-issued Faculty ID.</p>

          <form onSubmit={facultyLogin} noValidate>
            {sessionExpired && !error && (
              <Notice type="info">Your session expired. Please sign in again.</Notice>
            )}
            <Notice type="error">{error}</Notice>

            <div className="ft-field">
              <label className="ft-label" htmlFor="faculty-id">Faculty ID</label>
              <input
                id="faculty-id"
                className="ft-input"
                type="text"
                autoComplete="username"
                placeholder="e.g. FAC001"
                value={facultyId}
                onChange={(e) => setFacultyId(e.target.value)}
              />
            </div>

            <div className="ft-field">
              <label className="ft-label" htmlFor="faculty-password">Password</label>
              <input
                id="faculty-password"
                className="ft-input"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>

            <button className="ft-btn ft-btn--primary ft-btn--block" type="submit" disabled={loading}>
              {loading && <span className="ft-spinner" />}
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <div className="ft-divider"><span>Student?</span></div>

          <Link to="/student-portal" className="ft-student-link">
            <span>Student Portal<small>Attendance records and face status</small></span>
            <ChevronRightIcon size={18} />
          </Link>
        </section>
      </div>
    </PageShell>
  );
}

export default Login;
