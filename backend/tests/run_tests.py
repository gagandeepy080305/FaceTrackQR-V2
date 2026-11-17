"""
Run the backend test suites safely.

    python tests/run_tests.py            # all suites
    python tests/run_tests.py proxy scan # only suites whose name contains these

Why a runner instead of plain `python tests/test_x.py`: the suites reset the
database and wipe the uploads folder, so they must never run against your real
ones. This script therefore

  1. copies the backend (without uploads, .env or the venv) to a temp folder,
  2. starts a throwaway MongoDB on a free port (needs `mongod` on your PATH),
     or uses FT_TEST_MONGO_URI if you set it, and
  3. replaces DeepFace with the small stand-in in tests/fake, so no model
     download or GPU is needed and results are repeatable.

The real accuracy of the face model is measured separately with
tools/accuracy_report.py.

Needs the backend's Python packages (Flask, pymongo, itsdangerous, ...):
    pip install -r requirements.txt
"""

import glob
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
FAKE = os.path.join(HERE, "fake")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_mongod(workdir):
    """Start a throwaway mongod; returns (process, uri) or exits with advice."""

    mongod = shutil.which("mongod")

    if not mongod:
        sys.exit(
            "mongod was not found on your PATH.\n"
            "Install MongoDB (e.g. `brew install mongodb-community`), or point the tests at a\n"
            "throwaway MongoDB with FT_TEST_MONGO_URI=mongodb://127.0.0.1:<port>"
        )

    port = free_port()
    dbpath = os.path.join(workdir, "mongo")
    os.makedirs(dbpath)
    process = subprocess.Popen(
        [mongod, "--dbpath", dbpath, "--port", str(port), "--bind_ip", "127.0.0.1",
         "--logpath", os.path.join(workdir, "mongod.log")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    uri = f"mongodb://127.0.0.1:{port}"

    from pymongo import MongoClient

    for _ in range(60):
        try:
            MongoClient(uri, serverSelectionTimeoutMS=500).admin.command("ping")
            return process, uri
        except Exception:
            time.sleep(0.5)

    process.terminate()
    sys.exit("Could not start the temporary MongoDB (see mongod.log in the temp folder).")


def main():
    filters = [arg.lower() for arg in sys.argv[1:]]
    suites = sorted(glob.glob(os.path.join(HERE, "test_*.py")))

    if filters:
        suites = [s for s in suites if any(f in os.path.basename(s).lower() for f in filters)]

    if not suites:
        sys.exit("No test suites matched.")

    workdir = tempfile.mkdtemp(prefix="facetrack-tests-")
    mongod = None

    try:
        explicit = os.getenv("FT_TEST_MONGO_URI", "").strip()

        if explicit:
            # The suites drop the app's database, so refuse the default port
            # (where a real database usually lives) unless told otherwise.
            if ":27017" in explicit and os.getenv("FT_ALLOW_DEFAULT_MONGO") != "1":
                sys.exit(
                    "Refusing to run the tests on the default MongoDB port 27017: they erase the\n"
                    "database. Use a throwaway instance, or set FT_ALLOW_DEFAULT_MONGO=1 if you\n"
                    "are certain it holds nothing you need."
                )
            uri = explicit
        else:
            mongod, uri = start_mongod(workdir)

        app_copy = os.path.join(workdir, "backend")
        shutil.copytree(
            BACKEND, app_copy,
            ignore=shutil.ignore_patterns("venv", ".venv", "uploads", "tests", "__pycache__", ".env", ".secret_key", "*.pyc"),
        )
        os.makedirs(os.path.join(app_copy, "uploads", "faces"))

        env = dict(os.environ, FT_APP_DIR=app_copy, FT_FAKE_DIR=FAKE, FT_MONGO_URI=uri, FT_TMP_DIR=workdir,
                   PYTHONDONTWRITEBYTECODE="1")
        total_ok = total_fail = 0
        failed_suites = []

        for suite in suites:
            name = os.path.basename(suite)[:-3]
            result = subprocess.run([sys.executable, suite], env=env, capture_output=True, text=True)
            lines = result.stdout.splitlines()
            summary = next((l for l in reversed(lines) if " passed, " in l), None)
            failures = [l for l in lines if l.startswith("FAIL")]

            if summary is None:
                print(f"{name:16} CRASHED")
                print((result.stderr or result.stdout)[-1500:])
                failed_suites.append(name)
                continue

            passed, failed = int(summary.split()[0]), int(summary.split()[2])
            total_ok += passed
            total_fail += failed
            print(f"{name:16} {passed:4} passed, {failed} failed")

            for line in failures:
                print("    " + line[:220])

            if failed or result.returncode:
                failed_suites.append(name)

        print(f"\nTotal: {total_ok} passed, {total_fail} failed")
        return 1 if failed_suites else 0
    finally:
        if mongod:
            mongod.terminate()
            mongod.wait(timeout=20)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
