"""The Reach app: a small web server on this Mac that shows the app in your browser and runs its buttons.

Normally started by Reach.app (see tools-dev/make_app.py). For development: python3 app/server.py --open
It only listens on this Mac (127.0.0.1) and every request must carry a secret made fresh at each start,
so other websites open in your browser cannot press its buttons.
"""
import argparse
import json
import mimetypes
import os
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import actions  # noqa: E402

UI = Path(__file__).resolve().parent / "ui"
TOKEN = secrets.token_urlsafe(24)

actions.ensure_path()  # a Finder-launched shell often lacks Homebrew's folders


class Job:
    def __init__(self, name, title):
        self.id = secrets.token_hex(6)
        self.name, self.title = name, title
        self.lines, self.data, self.result = [], {}, None
        self.state, self.error, self.started = "running", "", time.time()
        self.on_line = lambda line: None

    def log(self, line):
        self.lines.append(line)
        del self.lines[:-400]

    def view(self):
        result = self.result
        if self.state == "done" and isinstance(result, dict) and result.get("client_secret"):
            # A device key's secret is handed to the screen once, then forgotten here.
            self.result = {k: v for k, v in result.items() if k != "client_secret"}
        return {"id": self.id, "name": self.name, "title": self.title, "state": self.state, "error": self.error,
                "log": self.lines[-200:], "data": self.data, "result": result, "started": self.started}


JOBS = {}
CURRENT = {"job": None}
LOCK = threading.Lock()


def start_job(name, args):
    title, fn = actions.ACTIONS[name]
    with LOCK:
        running = CURRENT["job"]
        if running and running.state == "running":
            return None, f"Wait for “{running.title}” to finish first."
        job = Job(name, title)
        JOBS[job.id] = job
        CURRENT["job"] = job

    def work():
        try:
            job.result = fn(args, job) or {}
            job.state = "done"
        except actions.StepError as e:
            job.error, job.state = str(e), "failed"
        except Exception as e:  # noqa: BLE001 — show anything unexpected instead of hanging
            job.error, job.state = f"Something unexpected went wrong: {e}", "failed"
            job.log(traceback.format_exc())
        finally:
            actions.clear_cache()

    threading.Thread(target=work, daemon=True).start()
    return job, ""


class Handler(BaseHTTPRequestHandler):
    server_version = "Reach"

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # No other page may show Reach inside a frame (it could trick you into clicking its buttons).
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _authed(self):
        return self._host_ok() and secrets.compare_digest(self.headers.get("X-Reach-Token", ""), TOKEN)

    def do_GET(self):
        try:
            self._get()
        except Exception as e:  # noqa: BLE001 - answer with the error instead of an empty page
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        try:
            self._post()
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _get(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        path = self.path.split("?")[0]
        if path == "/ping":
            return self._send(200, f"reach {VERSION} {Path(__file__).resolve().parent}".encode(), "text/plain")
        if path in ("/", "/index.html"):
            index = UI / "index.html"
            if not index.exists():  # the app was replaced or moved while this copy kept running
                threading.Thread(target=shutdown, daemon=True).start()
                return self._send(503, b"<h2>Reach was updated.</h2><p>Open Reach from Applications again.</p>",
                                  "text/html; charset=utf-8")
            html = index.read_text(encoding="utf-8").replace("__REACH_TOKEN__", TOKEN)
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if path.startswith("/ui/"):
            f = (UI / path[4:]).resolve()
            if UI in f.parents and f.is_file():
                return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return self._send(403, {"error": "forbidden"})
        try:
            if path == "/api/status":
                if "fresh" in self.path:
                    actions.clear_cache()
                s = actions.status()
                job = CURRENT["job"]
                s["job"] = job.view() if job else None
                return self._send(200, s)
            if path == "/api/log":
                f = actions.SUPPORT / "reach.log"
                lines = f.read_text(errors="replace").splitlines()[-60:] if f.exists() else []
                return self._send(200, {"log": "\n".join(lines)})
            if path.startswith("/api/job/"):
                job = JOBS.get(path.rsplit("/", 1)[-1])
                return self._send(200, job.view()) if job else self._send(404, {"error": "no such task"})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        self._send(404, {"error": "not found"})

    def _post(self):
        if not self._authed():
            return self._send(403, {"error": "forbidden"})
        if self.path == "/api/quit":
            self._send(200, {"ok": True})
            threading.Thread(target=shutdown, daemon=True).start()
            return
        if not self.path.startswith("/api/action/"):
            return self._send(404, {"error": "not found"})
        name = self.path.rsplit("/", 1)[-1]
        if name not in actions.ACTIONS:
            return self._send(404, {"error": "unknown action"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            args = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except ValueError:
            return self._send(400, {"error": "bad request"})
        job, err = start_job(name, args if isinstance(args, dict) else {})
        if not job:
            return self._send(409, {"error": err})
        self._send(200, job.view())


SERVER = {"srv": None}
PORT_FILE = actions.SUPPORT / "port"
CONTROL_FILE = actions.SUPPORT / "control"   # lets a newer launcher stop this copy; readable by this user only
VERSION = "dev"


def shutdown():
    time.sleep(0.3)
    for f, mine in ((PORT_FILE, str(SERVER["srv"].server_address[1])), (CONTROL_FILE, TOKEN)):
        try:
            if f.read_text().strip() == mine:
                f.unlink()
        except OSError:
            pass
    SERVER["srv"].shutdown()


def main():
    ap = argparse.ArgumentParser(description="Reach app")
    ap.add_argument("--port", type=int, default=8785)
    ap.add_argument("--open", action="store_true", help="open the app in the browser")
    ap.add_argument("--version", default="dev")
    a = ap.parse_args()
    global VERSION
    VERSION = a.version
    for port in range(a.port, a.port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    else:
        sys.exit("No free port for the app. Close other copies of it and try again.")
    SERVER["srv"] = srv
    actions.SUPPORT.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(port))
    CONTROL_FILE.write_text(TOKEN)
    os.chmod(CONTROL_FILE, 0o600)
    url = f"http://127.0.0.1:{port}/"
    print(f"Reach is running: {url}", flush=True)
    if a.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if PORT_FILE.read_text() == str(port):
                PORT_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
