"""Shared test setup: a private home folder, a fake keychain and launchctl (tests/bin), and the Cloudflare
stand-in (tests/fakecf.py) on a free local port. Import this first in every test file:

    from harness import A, ok, J, cf_state, cf_set, expect_error, finish, connect, tool_port
"""
import atexit, http.server, json, os, socket, subprocess, sys, tempfile, threading, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
_home = tempfile.mkdtemp(prefix="reach-test-")
os.environ.update(HOME=_home, FAKE_KC=os.path.join(_home, "kc"), PATH=os.path.join(HERE, "bin") + os.pathsep + os.environ["PATH"])

with socket.socket() as _s:
    _s.bind(("127.0.0.1", 0))
    PORT = _s.getsockname()[1]
_fake = subprocess.Popen([sys.executable, os.path.join(HERE, "fakecf.py"), str(PORT)], env=os.environ)
atexit.register(_fake.kill)
for _ in range(50):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/__state", timeout=1)
        break
    except OSError:
        time.sleep(0.1)
os.environ["REACH_CF_API"] = f"http://127.0.0.1:{PORT}"
sys.path.insert(0, os.path.join(ROOT, "app"))
import actions as A  # noqa: E402

FAILS = []


def ok(cond, message):
    print(("PASS " if cond else "FAIL ") + message, flush=True)
    if not cond:
        FAILS.append(message)


def finish():
    print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED")
    sys.exit(1 if FAILS else 0)


class J:
    """Stands in for a running task: collects its log lines."""
    def __init__(self):
        self.lines, self.data = [], {}

    def log(self, line):
        self.lines.append(line)


def cf_state():
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/__state").read())


def cf_set(**kw):
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/__set", data=json.dumps(kw).encode(), method="POST"))


def expect_error(fn, args, contains=""):
    """Runs an action that should refuse. Returns its message, None if it did not refuse, or 'WRONG: …'."""
    try:
        fn(args, J())
        return None
    except A.StepError as e:
        return str(e) if contains in str(e) else "WRONG: " + str(e)


def tool_port():
    """Starts a pretend local tool (an HTTP server) and returns its port."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def connect(zone="z1"):
    """Runs Set up end to end with a good key: key, domain, connector."""
    A.a_save_key({"token": "good"}, J())
    A.a_choose_zone({"zone_id": zone}, J())
    A.a_connect({}, J())
