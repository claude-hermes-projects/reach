"""Agent runner: builds one card at a time with a coding agent (Aider) and checks the result before anyone sees it.

    python3 tools-dev/agent_runner.py doctor       check every requirement; prints PASS/FAIL with what to fix
    python3 tools-dev/agent_runner.py once         build the next Ready card (Qwen cards first, then DeepSeek cards)
    python3 tools-dev/agent_runner.py once 12      build card #12 at its current level

Safety design
  - Model-written code never runs on this machine. Aider and the tests run in a sealed container (tools-dev/sandbox)
    holding a throwaway copy of the repository. The final test run has no network at all.
  - The GitHub key belongs to a separate bot account and never enters the container. GitHub's rules on main (a
    branch ruleset) stop the bot from merging or pushing to main: only the owner's approval merges a change.
  - Only the files a card lists may change; tests, workflows, dev tools and cards never. Changes that look like they
    contain a key or password are refused.
  - Levels: qwen (local) -> deepseek (hosted) -> claude (the card goes to Blocked). Two attempts per level.

Settings (no secrets): ~/.config/agent-runner/config.json
  {"repo": "<owner>/<repo>",
   "qwen_model": "lm_studio/<name>", "qwen_base": "http://<address the container can reach>:<port>/v1",
   "deepseek_model": "openrouter/<provider>/<model>", "image": "agent-sandbox", "attempts": 2, "minutes": 40}
Secrets: ~/.config/agent-runner/secrets.env (owner-only, mode 600): GH_TOKEN=<bot key>, OPENROUTER_API_KEY=<coding-only key with a credit limit>
"""
import json, os, re, shutil, stat, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import board  # noqa: E402

CONF_DIR = Path(os.environ.get("AGENT_RUNNER_HOME", Path.home() / ".config" / "agent-runner"))
NEXT = {"qwen": "deepseek", "deepseek": "claude"}
NEVER = ("tests/", ".github/", "tools-dev/", "cards/")
SECRET_PATTERNS = [r"sk-or-v1-[0-9a-f]{20,}", r"sk-[A-Za-z0-9]{32,}", r"gh[pousr]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}",
                   r"AKIA[0-9A-Z]{16}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"xox[baprs]-[A-Za-z0-9-]{10,}"]
RULES = """Rules (a script checks them afterwards; breaking one throws your work away):
- Change ONLY these files: {files}
- Never edit anything under tests/, .github/, tools-dev/ or cards/. Make the test pass by fixing the code.
- Keep the existing style: plain words in every message shown to people, and say what to do next.
- Never put keys, passwords or personal details in the code.
- Finished means this command passes: {test}
"""


class Stop(Exception):
    pass


# ------------------------------------------------------------------ settings and secrets

def load():
    cfg_file, sec_file = CONF_DIR / "config.json", CONF_DIR / "secrets.env"
    if not cfg_file.exists():
        raise Stop(f"Missing {cfg_file}. See tools-dev/HERMES-SETUP.md.")
    cfg = {"image": "agent-sandbox", "attempts": 2, "minutes": 40}
    cfg.update(json.loads(cfg_file.read_text()))
    sec = {}
    if sec_file.exists():
        mode = stat.S_IMODE(sec_file.stat().st_mode)
        if mode & 0o077:
            raise Stop(f"{sec_file} can be read by other users. Run: chmod 600 {sec_file}")
        for line in sec_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                sec[k.strip()] = v.strip().strip('"')
    if sec.get("GH_TOKEN"):
        os.environ["GH_TOKEN"] = sec["GH_TOKEN"]          # for gh on this machine only; never passed to the container
    return cfg, sec


def run(*cmd, cwd=None, check=True, timeout=900, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    if check and r.returncode != 0:
        raise Stop(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout).strip()[-600:]}")
    return r


def gh(*args, check=True):
    return run("gh", *args, check=check)


# ------------------------------------------------------------------ the sealed container

def sandbox(cfg, work, cmd, network, env=None, minutes=40):
    """Runs a command in the container with the throwaway copy mounted at /work. Nothing else of this machine.
    Without network (the final test run) the container is also read-only apart from /work and a scratch /tmp."""
    args = ["docker", "run", "--rm", "--network", "bridge" if network else "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "512", "--memory", "4g",
            "--user", f"{os.getuid()}:{os.getgid()}", "--tmpfs", "/tmp:rw,exec,size=1g",
            "-e", "HOME=/tmp", "-e", "GIT_AUTHOR_NAME=agent", "-e", "GIT_AUTHOR_EMAIL=agent@localhost",
            "-e", "GIT_COMMITTER_NAME=agent", "-e", "GIT_COMMITTER_EMAIL=agent@localhost",
            "-v", f"{work}:/work", "-w", "/work"]
    if not network:
        args.append("--read-only")
    for k, v in (env or {}).items():
        args += ["-e", f"{k}={v}"]
    args += [cfg["image"], *cmd]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=minutes * 60)
        return r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        return 124, f"Stopped after {minutes} minutes."


def model_env(cfg, sec, tier):
    if tier == "qwen":
        return cfg["qwen_model"], {"LM_STUDIO_API_BASE": cfg["qwen_base"], "LM_STUDIO_API_KEY": "lm-studio"}
    return cfg["deepseek_model"], {"OPENROUTER_API_KEY": sec.get("OPENROUTER_API_KEY", "")}


def empty_dir():
    return tempfile.mkdtemp(prefix="sandbox-check-")


def aider_flags(cfg):
    code, out = sandbox(cfg, empty_dir(), ["aider", "--help"], network=False, minutes=2)
    flags = ["--yes-always", "--no-check-update", "--no-show-model-warnings", "--no-auto-lint", "--no-gitignore"]
    if "--no-suggest-shell-commands" in out:
        flags.append("--no-suggest-shell-commands")
    return flags


# ------------------------------------------------------------------ cards

def section(body, name):
    m = re.search(rf"^##+ *{re.escape(name)}\s*\n(.*?)(?=^##|\Z)", body, re.S | re.M | re.I)
    return m.group(1).strip() if m else ""


def card(num):
    d = board.gh_json("issue", "view", str(num), "--json", "number,title,body,labels") or {}
    body = d.get("body") or ""
    files = [l.strip().strip("`- ") for l in section(body, "Files you may change").splitlines() if l.strip()]
    test = section(body, "Test command").splitlines()
    tier = next((l["name"][5:] for l in d.get("labels", []) if l["name"].startswith("tier:")), "qwen")
    return {"num": d.get("number", num), "title": d.get("title", ""), "body": body, "files": files,
            "test": test[0].strip().strip("`") if test else "", "tier": tier}


def safe_card(c):
    if not c["files"] or not c["test"]:
        return "the card needs “Files you may change” and “Test command” sections"
    if any(f.startswith(NEVER) or ".." in f or f.startswith("/") for f in c["files"]):
        return "the card lists a file agents may never change"
    if not re.fullmatch(r"python3 tests/run\.py( tests/test_[a-z0-9_]+\.py)+", c["test"]):
        return "the test command must look like: python3 tests/run.py tests/test_<name>.py"
    return ""


def comment(num, text):
    board.gh("issue", "comment", str(num), "--body", text[:60000], check=False)


# ------------------------------------------------------------------ checks on what the model wrote

def changed(work):
    a = run("git", "diff", "--name-only", "origin/main", cwd=work, check=False).stdout.split()
    b = [l[3:] for l in run("git", "status", "--porcelain", "--untracked-files=all", cwd=work, check=False).stdout.splitlines() if l.strip()]
    return sorted(set(a + b))


def secrets_in(work):
    diff = run("git", "diff", "origin/main", cwd=work, check=False).stdout
    untracked = [l[3:] for l in run("git", "status", "--porcelain", "--untracked-files=all", cwd=work, check=False).stdout.splitlines() if l.startswith("??")]
    for f in untracked:
        try:
            diff += (Path(work) / f).read_text(errors="ignore")
        except OSError:
            pass
    return [p for p in SECRET_PATTERNS if re.search(p, diff)]


# ------------------------------------------------------------------ one card

def handle(num, cfg, sec):
    c = card(num)
    tier = c["tier"]
    if tier not in NEXT:
        return "not for the runner"
    problem = safe_card(c)
    if problem:
        board.move(num, "blocked", "claude")
        comment(num, f"Runner: {problem}. Moved to Blocked.")
        return "blocked"
    board.move(num, "building")
    comment(num, f"Runner: started at level **{tier}**.")
    tmp = Path(tempfile.mkdtemp(prefix=f"card-{num}-"))
    work = tmp / "repo"
    try:
        run("gh", "repo", "clone", cfg["repo"], str(work), "--", "--quiet")
        branch = f"agent/card-{num}"
        run("git", "fetch", "-q", "origin", "main", cwd=work)
        run("git", "checkout", "-q", "-B", branch, "origin/main", cwd=work)     # always start from the latest main
        model, menv = model_env(cfg, sec, tier)
        flags = aider_flags(cfg)
        note, passed, log = "", False, ""
        for attempt in range(1, cfg["attempts"] + 1):
            prompt = f"{c['title']}\n\n{c['body']}\n\n" + RULES.format(files=", ".join(c["files"]), test=c["test"]) + note
            (work / ".card-prompt.md").write_text(prompt)
            code, out = sandbox(cfg, work, ["aider", "--model", model, *flags, "--test-cmd", c["test"], "--auto-test",
                                            "--message-file", ".card-prompt.md", *c["files"]],
                                network=True, env=menv, minutes=cfg["minutes"])
            (work / ".card-prompt.md").unlink(missing_ok=True)
            bad = [f for f in changed(work) if f not in c["files"] or f.startswith(NEVER)]
            leaks = secrets_in(work)
            if bad or leaks:
                run("git", "reset", "-q", "--hard", "origin/main", cwd=work)
                run("git", "clean", "-qfdx", cwd=work)
                why = (f"changed files it may not change ({', '.join(bad)})" if bad else "wrote something that looks like a key")
                note = f"\nYour previous attempt {why}. It was thrown away.\n"
                log = f"Attempt {attempt}: {why}.\n{out[-800:]}"
                continue
            # Final check: the card's test and the whole suite, in the container, with no network and no keys.
            code1, out1 = sandbox(cfg, work, ["python3", *c["test"].split()[1:]], network=False, minutes=15)
            code2, out2 = sandbox(cfg, work, ["python3", "tests/run.py"], network=False, minutes=15) if code1 == 0 else (1, "")
            log = f"$ {c['test']}\n{out1[-1500:]}\n\n$ python3 tests/run.py\n{out2[-1500:]}"
            if code1 == 0 and code2 == 0:
                passed = True
                break
            note = f"\nThe tests still fail. Their output:\n{log[-2000:]}\n"
        if not passed:
            up = NEXT[tier]
            board.move(num, "blocked" if up == "claude" else "ready", up)
            comment(num, f"Runner: level **{tier}** did not pass after {cfg['attempts']} attempts. Moved to **{up}**.\n\n```\n{log[-2500:]}\n```")
            return f"escalated to {up}"
        bot = gh("api", "user", "--jq", ".login").stdout.strip()
        run("git", "-c", f"user.name={bot}", "-c", f"user.email={bot}@users.noreply.github.com", "add", "-A", cwd=work)
        test_file = next((w for w in c["test"].split() if w.startswith("tests/test_card_")), "")
        pending = work / "cards" / "pending.txt"
        if pending.exists() and test_file:                      # the runner (not the model) retires the card's test
            pending.write_text("\n".join(l for l in pending.read_text().splitlines() if l.strip() != test_file) + "\n")
            run("git", "add", "cards/pending.txt", cwd=work)
        run("git", "-c", f"user.name={bot}", "-c", f"user.email={bot}@users.noreply.github.com",
            "commit", "-q", "-m", f"Card #{num}: {c['title']}", cwd=work, check=False)
        run("gh", "auth", "setup-git", check=False)
        run("git", "push", "--force", "origin", f"{branch}:{branch}", cwd=work)
        gh("pr", "create", "--repo", cfg["repo"], "--base", "main", "--head", branch, "--title", f"[#{num}] {c['title']}",
           "--body", f"Closes #{num}\n\nWritten at level **{tier}**. Final test run (sealed, no network):\n\n```\n{log[-2500:]}\n```")
        board.move(num, "review")
        comment(num, f"Runner: tests pass at level **{tier}**. Change request opened for review.")
        return "review"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def paused():
    """The pipeline switch: an open issue carrying the label "paused" stops all building until the label is removed."""
    return bool(board.gh_json("issue", "list", "--state", "open", "--label", "paused", "--json", "number", "--limit", "1"))


def once(num=None):
    cfg, sec = load()
    os.environ["GH_REPO"] = cfg["repo"]                       # board.py works on the main repository
    if paused():
        print("Paused: an issue has the label “paused”. Remove the label to resume.")
        return
    if num is None:
        num = board.next_card("qwen") or board.next_card("deepseek")
    if not num:
        print("No Ready cards for the runner.")
        return
    print(f"Card #{num}: {handle(num, cfg, sec)}")


# ------------------------------------------------------------------ doctor

def doctor():
    results = []
    def check(name, ok, fix=""):
        results.append(ok)
        print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"\n      Fix: {fix}"))
    try:
        cfg, sec = load()
    except Stop as e:
        check("settings and secrets files", False, str(e))
        return False
    check("settings file has every field", all(cfg.get(k) for k in ("repo", "qwen_model", "qwen_base", "deepseek_model")),
          "add repo, qwen_model, qwen_base and deepseek_model to config.json")
    check("secrets file has the bot key and the coding key", bool(sec.get("GH_TOKEN") and sec.get("OPENROUTER_API_KEY")),
          "add GH_TOKEN and OPENROUTER_API_KEY to secrets.env (mode 600)")
    check("docker works", run("docker", "version", check=False).returncode == 0, "install Docker and add this user to the docker group")
    img = run("docker", "image", "inspect", cfg["image"], "--format", "{{ index .Config.Labels \"agent.aider.version\" }}", check=False)
    check(f"sealed workspace built with a pinned Aider version ({img.stdout.strip() or 'none'})", img.returncode == 0 and bool(img.stdout.strip()),
          "docker build --build-arg AIDER_VERSION=<exact version> -t agent-sandbox tools-dev/sandbox")
    code, out = sandbox(cfg, empty_dir(), ["python3", "-c", "import urllib.request;urllib.request.urlopen('https://example.com',timeout=5)"], network=False, minutes=1)
    check("final test run has no network", code != 0, "the sandbox must be started with --network none")
    me = gh("api", "user", "--jq", ".login", check=False).stdout.strip()
    perms = board.gh_json("api", f"repos/{cfg['repo']}", "--jq", ".permissions") or {}
    check(f"GitHub key belongs to a bot account ({me or 'not signed in'}) without admin rights", bool(me) and not perms.get("admin"),
          "sign gh in with the bot account's fine-grained key, not the owner's")
    check("bot can push its own branches (collaborator with write access)", bool(perms.get("push")),
          "invite the bot as a collaborator on the repository and accept the invitation as the bot")
    rules = board.gh_json("api", f"repos/{cfg['repo']}/rules/branches/main") or []
    kinds = {r.get("type") for r in rules}
    check("main is protected: change request, owner approval and passing tests required",
          {"pull_request", "required_status_checks"} <= kinds, "the owner adds the branch ruleset on main (see HERMES-SETUP.md)")
    wf = gh("api", f"repos/{cfg['repo']}/contents/.github/workflows/checks.yml", "--jq", ".name", check=False)
    check("GitHub runs the tests on every change request", wf.returncode == 0, "the checks workflow is missing from main")
    code, out = sandbox(cfg, empty_dir(), ["python3", "-c", f"import urllib.request;print(urllib.request.urlopen('{cfg.get('qwen_base','')}/models',timeout=10).status)"],
                        network=True, minutes=1)
    check("the sealed workspace can reach Qwen in LM Studio", code == 0 and "200" in out,
          "make LM Studio's server reachable from containers at qwen_base (see HERMES-SETUP.md)")
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {sec.get('OPENROUTER_API_KEY','')}"})
        info = json.loads(urllib.request.urlopen(req, timeout=15).read()).get("data", {})
        check(f"coding key works and has a credit limit ({info.get('limit')})", info.get("limit") not in (None, 0),
              "create a coding-only OpenRouter key with a credit limit")
    except Exception as e:  # noqa: BLE001
        check("coding key works", False, f"OpenRouter refused the key ({e})")
    ok = all(results)
    print("\nREADY" if ok else "\nNOT READY: fix the FAIL lines, then run doctor again.")
    return ok


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["doctor"]:
            sys.exit(0 if doctor() else 1)
        elif sys.argv[1:2] == ["once"]:
            once(int(sys.argv[2]) if len(sys.argv) > 2 else None)
        else:
            print(__doc__)
            sys.exit(2)
    except Stop as e:
        print(f"Stopped: {e}")
        sys.exit(1)
