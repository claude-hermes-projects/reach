"""Everything Reach does. Plain Python calling Cloudflare's API and Cloudflare's connector program
(cloudflared), so it needs no extra libraries.

A "door" is one tool on this Mac (for example Ollama on port 11434) made reachable at one address on your
domain (for example ai.yourdomain.com). Every door sits behind Cloudflare Access: a person signs in with a
one-time email code, a device uses its own key. The sign-in rule is always created before the address
opens, and the connector on this Mac also refuses requests that did not pass the sign-in.

Secrets (the Cloudflare key, the connector's key) live in the macOS keychain, never in a file.
Actions never ask questions while running: everything they need arrives with the button press.
"""
import getpass
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SUPPORT = Path.home() / "Library" / "Application Support" / "Reach"
STATE_FILE = SUPPORT / "state.json"                 # settings only, no secrets
LOG_DIR = Path.home() / "Library" / "Logs" / "Reach"
KEY_SERVICE = "reach-cloudflare-api-token"           # keychain entry: Reach's Cloudflare key
TUNNEL_SERVICE = "reach-connector-token"             # keychain entry: the connector's own key
LABEL = "app.reach.connector"                        # background job that keeps the connector running
AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
SESSION = "24h"                                      # how long a sign-in lasts before a new email code
DEVICE_KEY_LIFE = "8760h"                            # device keys expire after a year

# Tests point Reach at a stand-in Cloudflare on this Mac; nothing else is accepted.
_api = os.environ.get("REACH_CF_API", "")
CF_API = _api if re.match(r"^http://127\.0\.0\.1:\d+$", _api) else "https://api.cloudflare.com/client/v4"

# Tools Reach recognises on this Mac. api=True: an API that checks the Host header, so the connector
# presents the request as local (as Ollama's own guide does for tunnels).
PRESETS = [
    {"port": 11434, "name": "Ollama", "sub": "ollama", "api": True},
    {"port": 1234, "name": "LM Studio", "sub": "lmstudio", "api": True},
    {"port": 8080, "name": "Open WebUI", "sub": "chat", "api": False},
    {"port": 3000, "name": "Open WebUI", "sub": "chat", "api": False},
    {"port": 8188, "name": "ComfyUI", "sub": "comfy", "api": False},
    {"port": 7860, "name": "Gradio app", "sub": "studio", "api": False},
    {"port": 3001, "name": "AnythingLLM", "sub": "anything", "api": False},
]
SUB_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$")
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[a-z]{2,}$", re.I)

_state_lock = threading.Lock()
_cache = {}


class StepError(Exception):
    """A problem to show the person, in plain words, with what to do next."""


# --------------------------------------------------------------------------- settings

def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"doors": []}


def save_state(st):
    with _state_lock:
        SUPPORT.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(STATE_FILE)


def door_by_id(st, did):
    d = next((d for d in st.get("doors", []) if d["id"] == did), None)
    if not d:
        raise StepError("That door no longer exists. Refresh the page.")
    return d


# --------------------------------------------------------------------------- keychain

def get_secret(service):
    if shutil.which("security"):
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    f = SUPPORT / f".{service}"                       # only where there is no keychain (development on Linux)
    return f.read_text().strip() if f.exists() else ""


def set_secret(service, value):
    if shutil.which("security"):
        r = subprocess.run(["security", "add-generic-password", "-a", getpass.getuser(), "-s", service, "-w", value, "-U"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise StepError("The Mac's keychain refused to save the key. Unlock the keychain and try again.")
        return
    SUPPORT.mkdir(parents=True, exist_ok=True)
    f = SUPPORT / f".{service}"
    f.write_text(value)
    os.chmod(f, 0o600)


def delete_secret(service):
    if shutil.which("security"):
        subprocess.run(["security", "delete-generic-password", "-s", service], capture_output=True, text=True)
    else:
        (SUPPORT / f".{service}").unlink(missing_ok=True)


# --------------------------------------------------------------------------- Cloudflare

def cf(method, path, data=None, token=None):
    token = token or get_secret(KEY_SERVICE)
    if not token:
        raise StepError("No Cloudflare key saved yet. Finish “Cloudflare access” in Set up.")
    req = urllib.request.Request(CF_API + path, method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode() or "{}")
        except ValueError:
            return {"success": False, "errors": [{"message": f"HTTP {e.code}"}]}
    except urllib.error.URLError as e:
        raise StepError(f"Could not reach Cloudflare ({e.reason}). Check the internet connection.")


def cf_ok(method, path, data=None, what="Cloudflare"):
    r = cf(method, path, data)
    if not r.get("success"):
        raise StepError(f"{what} failed: {cf_error(r)}")
    return r.get("result")


def cf_error(resp):
    errs = resp.get("errors") or []
    return "; ".join(str(e.get("message", e)) for e in errs) or "unknown error"


# --------------------------------------------------------------------------- running tools

def ensure_path():
    """Finder-launched apps often miss Homebrew's folders (and Homebrew may be installed while the app runs)."""
    parts = os.environ.get("PATH", "").split(":")
    for extra in ("/usr/local/bin", "/opt/homebrew/bin"):
        if Path(extra).is_dir() and extra not in parts:
            parts.insert(0, extra)
    os.environ["PATH"] = ":".join(parts)


def run(cmd, job=None, input=None, check=True, quiet=False, label=None, timeout=300):
    """Runs a command, streaming its output into the task log. Returns (exit code, output)."""
    ensure_path()
    if job and not quiet:
        job.log("› " + (label or " ".join(shlex.quote(c) for c in cmd)))
    try:
        p = subprocess.run(cmd, input=input, text=True, capture_output=True, timeout=timeout, cwd=str(Path.home()))
    except FileNotFoundError:
        raise StepError(f"{cmd[0]} is not installed. Open Set up and install the missing tools.")
    except subprocess.TimeoutExpired:
        raise StepError(f"{label or cmd[0]} took too long and was stopped.")
    out = (p.stdout or "") + (p.stderr or "")
    if job and not quiet:
        for line in out.splitlines()[-30:]:
            job.log("  " + line)
    if check and p.returncode != 0:
        tail = "\n".join(out.splitlines()[-6:]).strip()
        raise StepError(f"{label or ' '.join(cmd[:2])} failed." + (f"\n{tail}" if tail else ""))
    return p.returncode, out


def cloudflared_path():
    ensure_path()
    return shutil.which("cloudflared") or ""


def have(tool):
    ensure_path()
    return shutil.which(tool) is not None


def port_answers(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.3):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------- the connector (cloudflared)

def agent_plist_text(binary):
    # The connector's key is read from the keychain each time it starts, so it is never written to a file.
    script = (f'export TUNNEL_TOKEN="$(/usr/bin/security find-generic-password -s {TUNNEL_SERVICE} -w)"; '
              f'exec "{binary}" tunnel --no-autoupdate run')
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>-c</string><string>{esc(script)}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>{esc(str(LOG_DIR / "connector.log"))}</string>
  <key>StandardErrorPath</key><string>{esc(str(LOG_DIR / "connector.log"))}</string>
</dict>
</plist>
"""


def gui_domain():
    return f"gui/{os.getuid()}"


def connector_loaded():
    if not have("launchctl"):
        return False
    code, _ = run(["launchctl", "print", f"{gui_domain()}/{LABEL}"], check=False, quiet=True, timeout=10)
    return code == 0


def connector_start(job):
    binary = cloudflared_path()
    if not binary:
        raise StepError("Cloudflare's connector program isn't installed. Open Set up → Tools.")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    AGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
    AGENT_PLIST.write_text(agent_plist_text(binary), encoding="utf-8")
    if connector_loaded():
        run(["launchctl", "bootout", f"{gui_domain()}/{LABEL}"], job, check=False, quiet=True, timeout=20)
        time.sleep(1)
    run(["launchctl", "bootstrap", gui_domain(), str(AGENT_PLIST)], job, label="Start the connector")


def connector_stop(job):
    if connector_loaded():
        run(["launchctl", "bootout", f"{gui_domain()}/{LABEL}"], job, label="Stop the connector", timeout=20)
    AGENT_PLIST.unlink(missing_ok=True)          # so it stays off after a restart too, until turned on again


def tunnel_health(st):
    if not st.get("tunnel_id"):
        return {"status": "none", "connections": 0}
    r = cf("GET", f"/accounts/{st['account_id']}/cfd_tunnel/{st['tunnel_id']}")
    t = r.get("result") or {}
    return {"status": t.get("status", "unknown") if r.get("success") else "unknown",
            "connections": len(t.get("connections") or [])}


def push_config(st, job=None):
    """Tells Cloudflare which addresses lead to which tools. Only open doors are listed; anything else gets
    'not found'. Each door also makes the connector check the sign-in itself (origin check)."""
    team = (st.get("team_domain") or "").split(".")[0]
    def rules(with_check):
        out = []
        for d in st.get("doors", []):
            if not d.get("open"):
                continue
            origin = {}
            if d.get("api"):
                origin["httpHostHeader"] = f"localhost:{d['port']}"
            if with_check and d.get("aud") and team:
                origin["access"] = {"required": True, "teamName": team, "audTag": [d["aud"]]}
            out.append({"hostname": d["host"], "service": f"http://localhost:{d['port']}", "originRequest": origin})
        out.append({"service": "http_status:404"})
        return out
    path = f"/accounts/{st['account_id']}/cfd_tunnel/{st['tunnel_id']}/configurations"
    r = cf("PUT", path, {"config": {"ingress": rules(True)}})
    check = True
    if not r.get("success"):
        # Sign-in at Cloudflare still applies; only the second check on this Mac is missing.
        r = cf("PUT", path, {"config": {"ingress": rules(False)}})
        check = False
        if not r.get("success"):
            raise StepError(f"Cloudflare didn't accept the door list: {cf_error(r)}")
    if check and any(d.get("open") for d in st.get("doors", [])):
        got = cf("GET", path).get("result") or {}
        ing = ((got.get("config") or {}).get("ingress") or [])
        check = all(((x.get("originRequest") or {}).get("access") or {}).get("required") for x in ing if x.get("hostname"))
    st["origin_check"] = check
    if job:
        job.log("Door list sent to Cloudflare" + ("" if check else " (second sign-in check on this Mac unavailable)") + ".")


# --------------------------------------------------------------------------- status

def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except StepError:
        val = hit[1] if hit else None
    _cache[key] = (time.time(), val)
    return val


def clear_cache():
    _cache.clear()


def detected(st):
    used = {d["port"] for d in st.get("doors", [])}
    seen, out = set(), []
    for p in PRESETS:
        if p["port"] in used or p["port"] in seen:
            continue
        if port_answers(p["port"]):
            seen.add(p["port"])
            out.append(p)
    return out


def status():
    st = load_state()
    key = bool(get_secret(KEY_SERVICE))
    tools = {"cloudflared": bool(cloudflared_path()), "brew": have("brew")}
    steps = {
        "tools": tools["cloudflared"],
        "cloudflare": bool(key and st.get("account_id") and st.get("team_domain")),
        "domain": bool(st.get("zone_id")),
        "connect": bool(st.get("tunnel_id") and st.get("connector")),
    }
    loaded = connector_loaded() if steps["connect"] else False
    health = cached("health", 20, lambda: tunnel_health(st)) if (steps["connect"] and key) else None
    doors = []
    for d in st.get("doors", []):
        doors.append({k: d.get(k) for k in ("id", "name", "host", "port", "open", "emails", "api")} |
                     {"local": port_answers(d["port"]), "url": f"https://{d['host']}",
                      "devices": [{k: v.get(k) for k in ("id", "name", "client_id", "created")} for v in d.get("devices", [])]})
    return {
        "tools": tools,
        "steps": steps,
        "setup_done": all(steps.values()),
        "account": st.get("account_name", ""),
        "team_domain": st.get("team_domain", ""),
        "zone": st.get("zone", ""),
        "zones": st.get("zones_seen", []),
        "connector": {"wanted": st.get("connector") == "on", "running": loaded,
                      "cloud": (health or {}).get("status", "none"), "connections": (health or {}).get("connections", 0)},
        "origin_check": st.get("origin_check", True),
        "doors": doors,
        "detected": detected(st) if steps["connect"] else [],
        "presets": PRESETS,
    }


# --------------------------------------------------------------------------- set up

def a_install_tools(args, job):
    if cloudflared_path():
        job.log("Cloudflare's connector program is already installed.")
        return {}
    if not have("brew"):
        raise StepError("Homebrew, the free installer for Mac tools, is needed first. Follow the steps shown above.")
    run(["brew", "install", "cloudflared"], job, label="Install Cloudflare's connector program", timeout=900)
    return {}


NEEDED = [
    ("tunnel", "Account · Cloudflare Tunnel · Edit"),
    ("apps", "Account · Access: Apps and Policies · Edit"),
    ("tokens", "Account · Access: Service Tokens · Edit"),
    ("idps", "Account · Access: Organizations, Identity Providers, and Groups · Edit"),
    ("zones", "Zone · DNS · Edit (Zone Resources: your domain)"),
]


def a_save_key(args, job):
    token = (args.get("token") or "").strip()
    if not token:
        raise StepError("Paste the Cloudflare key first.")
    job.log("Checking the key with Cloudflare…")
    if not cf("GET", "/user/tokens/verify", token=token).get("success"):
        raise StepError("Cloudflare did not accept this key. Copy it again (it is shown only once) or create a new one.")
    # Find the account: from the account list, else from the domain's record (some keys can't list accounts),
    # else from the Account ID pasted on the screen.
    accounts = cf("GET", "/accounts", token=token).get("result") or []
    if not accounts:
        zs = cf("GET", "/zones?per_page=50", token=token).get("result") or []
        accounts = list({z["account"]["id"]: z["account"] for z in zs if (z.get("account") or {}).get("id")}.values())
    typed = (args.get("account_id") or "").strip().lower()
    if typed:
        if not re.match(r"^[0-9a-f]{32}$", typed):
            raise StepError("The Account ID is 32 letters and digits. Copy it again from the Cloudflare dashboard.")
        accounts = [a for a in accounts if a["id"] == typed] or [{"id": typed, "name": ""}]
    if not accounts:
        raise StepError("The key works, but Cloudflare didn't say which account it belongs to. Paste your Account ID "
                        "in the extra field below the key and press the button again. To find it: Cloudflare "
                        "dashboard → Account home → ⋯ next to your account name → Copy account ID.")
    acc = accounts[0]["id"]
    org = cf("GET", f"/accounts/{acc}/access/organizations", token=token)
    team = (org.get("result") or {}).get("auth_domain", "") if org.get("success") else ""
    probes = {
        "tunnel": cf("GET", f"/accounts/{acc}/cfd_tunnel?per_page=1", token=token),
        "apps": cf("GET", f"/accounts/{acc}/access/apps", token=token),
        "tokens": cf("GET", f"/accounts/{acc}/access/service_tokens", token=token),
        "idps": cf("GET", f"/accounts/{acc}/access/identity_providers", token=token),
        "zones": cf("GET", "/zones?per_page=50", token=token),
    }
    if not team and probes["idps"].get("success") is False and "organization" in cf_error(probes["idps"]).lower():
        raise StepError("Cloudflare Zero Trust isn't set up on this account yet. Do part A above (team name and the "
                        "Free plan), then press this button again.")
    missing = [label for k, label in NEEDED if not probes[k].get("success")]
    if missing:
        raise StepError("The key is missing these permission rows: " + "; ".join(missing) +
                        ". In Cloudflare: ⋯ next to the key → Edit → add them → Continue to summary → save. "
                        "Then press this button again (no need to paste a new key).")
    if not team:
        raise StepError("Cloudflare Zero Trust isn't set up on this account yet. Do part A above (team name and the "
                        "Free plan), then press this button again.")
    idps = probes["idps"].get("result") or []
    if not any(i.get("type") == "onetimepin" for i in idps):
        r = cf("POST", f"/accounts/{acc}/access/identity_providers",
               {"name": "One-time PIN", "type": "onetimepin", "config": {}}, token=token)
        if not r.get("success"):
            raise StepError(f"Couldn't switch on email sign-in codes: {cf_error(r)}")
        job.log("Switched on sign-in with one-time email codes.")
    zones = [{"id": z["id"], "name": z["name"]} for z in (probes["zones"].get("result") or [])]
    if not zones:
        raise StepError("The key can't see any domain. Add your domain to Cloudflare first, and set the key's Zone "
                        "Resources to Include · Specific zone · your domain.")
    set_secret(KEY_SERVICE, token)
    st = load_state()
    st.update(account_id=acc, account_name=accounts[0].get("name", ""), team_domain=team, zones_seen=zones)
    save_state(st)
    job.log(f"Key saved in the keychain. Sign-in page: {team}")
    return {}


def a_choose_zone(args, job):
    st = load_state()
    zid = args.get("zone_id") or ""
    zone = next((z for z in st.get("zones_seen", []) if z["id"] == zid), None)
    if not zone:
        raise StepError("Pick one of the domains in the list.")
    r = cf("GET", f"/zones/{zid}/dns_records?per_page=1")
    if not r.get("success"):
        raise StepError(f"The key can't manage addresses on {zone['name']}: add Zone · DNS · Edit for that domain.")
    if st.get("zone_id") and st["zone_id"] != zid and st.get("doors"):
        raise StepError("Remove the existing doors before switching to another domain.")
    st.update(zone_id=zid, zone=zone["name"])
    save_state(st)
    return {}


def a_connect(args, job):
    st = load_state()
    if not (st.get("account_id") and st.get("zone_id")):
        raise StepError("Finish the earlier steps first.")
    acc = st["account_id"]
    if not st.get("tunnel_id"):
        name = "reach-" + secrets.token_hex(3)
        job.log("Creating the connection at Cloudflare…")
        t = cf_ok("POST", f"/accounts/{acc}/cfd_tunnel", {"name": name, "config_src": "cloudflare"}, "Creating the connection")
        token = t.get("token") or cf_ok("GET", f"/accounts/{acc}/cfd_tunnel/{t['id']}/token", what="Getting its key")
        set_secret(TUNNEL_SERVICE, token)
        st.update(tunnel_id=t["id"], tunnel_name=name)
        save_state(st)
    elif not get_secret(TUNNEL_SERVICE):
        token = cf_ok("GET", f"/accounts/{acc}/cfd_tunnel/{st['tunnel_id']}/token", what="Getting the connection's key")
        set_secret(TUNNEL_SERVICE, token)
    push_config(st, job)
    st["connector"] = "on"
    save_state(st)
    connector_start(job)
    job.log("Waiting for the connector to reach Cloudflare…")
    for _ in range(15):
        h = tunnel_health(st)
        if h["status"] == "healthy":
            job.log("Connected.")
            return {}
        time.sleep(2)
    raise StepError("The connector started but hasn't reached Cloudflare yet. Check this Mac is online, wait a "
                    "minute and press Check status. Its log is in your home folder: Library › Logs › Reach › connector.log")


def a_connector(args, job):
    """The off switch (on=false closes every door at once) and turning it back on."""
    st = load_state()
    if not st.get("tunnel_id"):
        raise StepError("Finish Set up first.")
    if args.get("on"):
        st["connector"] = "on"
        save_state(st)
        connector_start(job)
        job.log("Connector on. Open doors are reachable again.")
    else:
        st["connector"] = "off"
        save_state(st)
        connector_stop(job)
        job.log("Connector off. Nothing on this Mac is reachable from outside until you turn it on.")
    clear_cache()
    return {}


# --------------------------------------------------------------------------- doors

def clean_emails(raw):
    items = [e.strip().lower() for e in re.split(r"[\s,;]+", raw or "") if e.strip()]
    bad = [e for e in items if not EMAIL_RE.match(e)]
    if bad:
        raise StepError("These don't look like email addresses: " + ", ".join(bad))
    if not items:
        raise StepError("Add at least one email address that may sign in.")
    return list(dict.fromkeys(items))


def ensure_dns(st, d):
    z, target = st["zone_id"], f"{st['tunnel_id']}.cfargotunnel.com"
    existing = (cf("GET", f"/zones/{z}/dns_records?name={d['host']}").get("result") or [])
    for rec in existing:
        if rec.get("type") == "CNAME" and rec.get("content") == target:
            d["dns_id"] = rec["id"]
            return
    if existing:
        raise StepError(f"{d['host']} is already used by another record in Cloudflare. Pick a different short name.")
    rec = cf_ok("POST", f"/zones/{z}/dns_records",
                {"type": "CNAME", "name": d["host"], "content": target, "proxied": True,
                 "comment": "Reach door"}, f"Creating the address {d['host']}")
    d["dns_id"] = rec["id"]


def a_door_add(args, job):
    st = load_state()
    if not (st.get("tunnel_id") and st.get("zone")):
        raise StepError("Finish Set up first.")
    name = (args.get("name") or "").strip()[:60] or "Door"
    sub = (args.get("sub") or "").strip().lower()
    if not SUB_RE.match(sub):
        raise StepError("The short name may use lowercase letters, digits and hyphens, like ai or chat-1.")
    try:
        port = int(args.get("port"))
        assert 1 <= port <= 65535
    except (TypeError, ValueError, AssertionError):
        raise StepError("The port must be a number between 1 and 65535.")
    emails = clean_emails(args.get("emails"))
    host = f"{sub}.{st['zone']}"
    if any(d["host"] == host for d in st["doors"]):
        raise StepError(f"{host} is already a door.")
    preset = next((p for p in PRESETS if p["port"] == port), None)
    api = bool(args.get("api")) if "api" in args else bool(preset and preset["api"])
    acc = st["account_id"]

    # 1. The sign-in rule comes first: nothing is reachable without it.
    job.log(f"Creating the sign-in rule for {host}…")
    app = cf_ok("POST", f"/accounts/{acc}/access/apps",
                {"name": f"Reach · {name}", "domain": host, "type": "self_hosted", "session_duration": SESSION,
                 "app_launcher_visible": False}, "Creating the sign-in rule")
    pol = cf("POST", f"/accounts/{acc}/access/apps/{app['id']}/policies",
             {"name": "People who may sign in", "decision": "allow", "precedence": 1,
              "include": [{"email": {"email": e}} for e in emails]})
    if not pol.get("success"):
        cf("DELETE", f"/accounts/{acc}/access/apps/{app['id']}")
        raise StepError(f"Cloudflare refused the list of people: {cf_error(pol)}")
    d = {"id": secrets.token_hex(4), "name": name, "sub": sub, "host": host, "port": port, "api": api,
         "open": False, "app_id": app["id"], "aud": app.get("aud", ""), "policy_id": pol["result"]["id"],
         "emails": emails, "devices": [], "next_prec": 2}
    st["doors"].append(d)
    save_state(st)

    # 2. Then the address and the route to this Mac.
    try:
        ensure_dns(st, d)
        d["open"] = True
        push_config(st, job)
    except StepError:
        d["open"] = False
        save_state(st)
        raise
    save_state(st)
    job.log(f"Open: https://{host} (sign-in with an email code).")
    if not port_answers(port):
        job.log(f"Note: nothing is answering on port {port} on this Mac right now. Start the tool and the door works.")
    return {"url": f"https://{host}"}


def a_door_open(args, job):
    st = load_state()
    d = door_by_id(st, args.get("id"))
    if not d.get("app_id"):
        raise StepError("This door has no sign-in rule. Remove it and add it again.")
    ensure_dns(st, d)
    d["open"] = True
    push_config(st, job)
    save_state(st)
    return {}


def a_door_close(args, job):
    st = load_state()
    d = door_by_id(st, args.get("id"))
    d["open"] = False
    push_config(st, job)
    save_state(st)
    job.log(f"{d['host']} is closed. Its settings are kept; press Open to reopen it.")
    return {}


def a_door_people(args, job):
    st = load_state()
    d = door_by_id(st, args.get("id"))
    emails = clean_emails(args.get("emails"))
    cf_ok("PUT", f"/accounts/{st['account_id']}/access/apps/{d['app_id']}/policies/{d['policy_id']}",
          {"name": "People who may sign in", "decision": "allow", "precedence": 1,
           "include": [{"email": {"email": e}} for e in emails]}, "Updating the people list")
    d["emails"] = emails
    save_state(st)
    job.log("Updated. People already signed in keep access until their sign-in expires (24 hours).")
    return {}


def a_door_remove(args, job):
    st = load_state()
    d = door_by_id(st, args.get("id"))
    acc = st["account_id"]
    d["open"] = False
    push_config(st, job)                                   # first: no route to this Mac
    if d.get("dns_id"):
        cf("DELETE", f"/zones/{st['zone_id']}/dns_records/{d['dns_id']}")
    for dev in d.get("devices", []):
        cf("DELETE", f"/accounts/{acc}/access/service_tokens/{dev['token_id']}")
    if d.get("app_id"):
        cf("DELETE", f"/accounts/{acc}/access/apps/{d['app_id']}")
    st["doors"] = [x for x in st["doors"] if x["id"] != d["id"]]
    save_state(st)
    job.log(f"Removed {d['host']}.")
    return {}


# --------------------------------------------------------------------------- device keys

def a_device_add(args, job):
    st = load_state()
    d = door_by_id(st, args.get("id"))
    name = (args.get("name") or "").strip()[:40]
    if not name:
        raise StepError("Give the device a name, like Laptop or Phone script.")
    acc = st["account_id"]
    tok = cf_ok("POST", f"/accounts/{acc}/access/service_tokens",
                {"name": f"Reach · {d['name']} · {name}", "duration": DEVICE_KEY_LIFE}, "Creating the device key")
    pol = cf("POST", f"/accounts/{acc}/access/apps/{d['app_id']}/policies",
             {"name": f"Device: {name}", "decision": "non_identity", "precedence": d.get("next_prec", 2),
              "include": [{"service_token": {"token_id": tok["id"]}}]})
    if not pol.get("success"):
        cf("DELETE", f"/accounts/{acc}/access/service_tokens/{tok['id']}")
        raise StepError(f"Cloudflare refused the device rule: {cf_error(pol)}")
    d["next_prec"] = d.get("next_prec", 2) + 1
    d["devices"].append({"id": secrets.token_hex(4), "name": name, "token_id": tok["id"],
                         "policy_id": pol["result"]["id"], "client_id": tok.get("client_id", ""),
                         "created": time.strftime("%Y-%m-%d")})
    save_state(st)
    job.log(f"Key for {name} created. Copy it now: it is shown only once.")
    return {"door": d["id"], "device": name, "url": f"https://{d['host']}",
            "client_id": tok.get("client_id", ""), "client_secret": tok.get("client_secret", "")}


def a_device_remove(args, job):
    st = load_state()
    d = door_by_id(st, args.get("id"))
    dev = next((v for v in d.get("devices", []) if v["id"] == args.get("device")), None)
    if not dev:
        raise StepError("That device key no longer exists. Refresh the page.")
    acc = st["account_id"]
    cf("DELETE", f"/accounts/{acc}/access/apps/{d['app_id']}/policies/{dev['policy_id']}")
    cf("DELETE", f"/accounts/{acc}/access/service_tokens/{dev['token_id']}")
    d["devices"] = [v for v in d["devices"] if v["id"] != dev["id"]]
    save_state(st)
    job.log(f"Key for {dev['name']} removed. That device can no longer get in.")
    return {}


ACTIONS = {
    "install_tools": ("Install Cloudflare's connector program", a_install_tools),
    "save_key": ("Check and save the Cloudflare key", a_save_key),
    "choose_zone": ("Use this domain", a_choose_zone),
    "connect": ("Connect this Mac", a_connect),
    "connector": ("Switch the connector", a_connector),
    "door_add": ("Add a door", a_door_add),
    "door_open": ("Open the door", a_door_open),
    "door_close": ("Close the door", a_door_close),
    "door_people": ("Update who may sign in", a_door_people),
    "door_remove": ("Remove the door", a_door_remove),
    "device_add": ("Create a device key", a_device_add),
    "device_remove": ("Remove a device key", a_device_remove),
}
