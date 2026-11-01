"""The build board: GitHub issues are the cards, labels are the truth, a GitHub Project board mirrors them.

    python3 tools-dev/board.py setup              labels, the "Builds" project with a Stage field, cards from cards/*.md
    python3 tools-dev/board.py move 12 review     move card #12 (labels + board)
    python3 tools-dev/board.py next qwen          oldest Ready card for that level (prints its number, or nothing)
    python3 tools-dev/board.py report             one line per open card, grouped by stage

Needs GitHub's tool (gh) signed in with the "project" permission: gh auth refresh -s project
Run inside the repository folder.
"""
import json, os, re, subprocess, sys
from pathlib import Path

STAGES = ["inbox", "ready", "building", "review", "your-test", "done", "blocked"]
STAGE_NAMES = {"inbox": "Inbox", "ready": "Ready", "building": "Building", "review": "Review",
               "your-test": "Your test", "done": "Done", "blocked": "Blocked"}
TIERS = ["qwen", "deepseek", "claude"]
COLORS = {"inbox": "ededed", "ready": "0e8a16", "building": "fbca04", "review": "1d76db", "your-test": "5319e7",
          "done": "0b6e34", "blocked": "b60205", "qwen": "c5def5", "deepseek": "bfd4f2", "claude": "d4c5f9"}
PROJECT_TITLE = "Builds"


def gh(*args, check=True, input=None):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, input=input)
    if check and r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:3])} failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return r


def gh_json(*args):
    r = gh(*args, check=False)
    try:
        return json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        return None


def repo():
    return gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").stdout.strip()


# ------------------------------------------------------------------ the project board (a mirror; best effort)

def project():
    data = gh_json("project", "list", "--owner", "@me", "--format", "json") or {}
    return next((p for p in data.get("projects", []) if p.get("title") == PROJECT_TITLE), None)


def stage_field(num):
    data = gh_json("project", "field-list", str(num), "--owner", "@me", "--format", "json") or {}
    return next((f for f in data.get("fields", []) if f.get("name") == "Stage"), None)


def board_set(issue_url, stage):
    """Puts the card in the right column of the board. Labels already hold the truth, so failures only warn."""
    try:
        p = project()
        if not p:
            return "no board"
        f = stage_field(p["number"])
        opt = next((o for o in (f or {}).get("options", []) if o["name"] == STAGE_NAMES[stage]), None)
        item = gh_json("project", "item-add", str(p["number"]), "--owner", "@me", "--url", issue_url, "--format", "json")
        if not (f and opt and item):
            return "board not updated"
        gh("project", "item-edit", "--id", item["id"], "--project-id", p["id"], "--field-id", f["id"],
           "--single-select-option-id", opt["id"], check=False)
        return "board updated"
    except SystemExit as e:
        return f"board not updated ({e})"


# ------------------------------------------------------------------ cards

def labels_of(num):
    data = gh_json("issue", "view", str(num), "--json", "labels,url") or {}
    return [l["name"] for l in data.get("labels", [])], data.get("url", "")


def move(num, stage, tier=None):
    if stage not in STAGES:
        raise SystemExit(f"Unknown stage {stage}. Use one of: {', '.join(STAGES)}")
    have, url = labels_of(num)
    drop = [l for l in have if l.startswith("stage:") and l != f"stage:{stage}"]
    if tier:
        drop += [l for l in have if l.startswith("tier:") and l != f"tier:{tier}"]
    args = ["issue", "edit", str(num), "--add-label", f"stage:{stage}" + (f",tier:{tier}" if tier else "")]
    for l in drop:
        args += ["--remove-label", l]
    gh(*args)
    if stage == "done":
        gh("issue", "close", str(num), check=False)
    return board_set(url, stage) if url else "no url"


def next_card(tier):
    data = gh_json("issue", "list", "--state", "open", "--label", "stage:ready", "--label", f"tier:{tier}",
                   "--json", "number,createdAt", "--limit", "100") or []
    data.sort(key=lambda i: (i["createdAt"], i["number"]))
    return data[0]["number"] if data else None


def report():
    data = gh_json("issue", "list", "--state", "open", "--json", "number,title,labels", "--limit", "200") or []
    by = {s: [] for s in STAGES}
    for i in data:
        names = [l["name"] for l in i["labels"]]
        st = next((n[6:] for n in names if n.startswith("stage:")), "inbox")
        tier = next((n[5:] for n in names if n.startswith("tier:")), "")
        by.setdefault(st, []).append(f"#{i['number']} {i['title']}" + (f" [{tier}]" if tier else ""))
    lines = []
    for s in STAGES:
        if by.get(s):
            lines.append(f"{STAGE_NAMES[s]}: " + "; ".join(by[s]))
    return "\n".join(lines) or "No open cards."


def parse_card(text):
    title = re.search(r"^# (.+)$", text, re.M)
    tier = re.search(r"^Tier: *(\w+)", text, re.M)
    return (title.group(1).strip() if title else "Untitled card"), (tier.group(1).lower() if tier else "claude")


def setup():
    for s in STAGES:
        gh("label", "create", f"stage:{s}", "--color", COLORS[s], "--description", f"Board column: {STAGE_NAMES[s]}", "--force")
    for t in TIERS:
        gh("label", "create", f"tier:{t}", "--color", COLORS[t], "--description", f"Who codes it: {t}", "--force")
    gh("label", "create", "paused", "--color", "b60205", "--description", "Pipeline switch: on an open issue, stops all building", "--force")
    print("Labels ready.")
    switch_title = "Pipeline switch"
    existing_titles = {i["title"] for i in (gh_json("issue", "list", "--state", "open", "--json", "title", "--limit", "500") or [])}
    if switch_title not in existing_titles:
        gh("issue", "create", "--title", switch_title, "--body",
           "Add the label **paused** to this issue to stop all agent building at once (it takes effect at the "
           "runner's next check, within 15 minutes). Remove the label to resume. Nothing else happens here.", check=False)
        print("Created the “Pipeline switch” issue (pin it on GitHub for one-tap access).")
    p = project()
    if not p:
        p = gh_json("project", "create", "--owner", "@me", "--title", PROJECT_TITLE, "--format", "json")
        print(f"Created the board “{PROJECT_TITLE}”." if p else
              "Couldn't create the board (run: gh auth refresh -s project). Cards and labels work without it.")
    if p and not stage_field(p["number"]):
        gh("project", "field-create", str(p["number"]), "--owner", "@me", "--name", "Stage", "--data-type",
           "SINGLE_SELECT", "--single-select-options", ",".join(STAGE_NAMES[s] for s in STAGES), check=False)
    if p:
        gh("project", "link", str(p["number"]), "--owner", "@me", "--repo", repo(), check=False)
    existing = {i["title"] for i in (gh_json("issue", "list", "--state", "all", "--json", "title", "--limit", "500") or [])}
    for f in sorted(Path("cards").glob("[0-9]*.md")):
        title, tier = parse_card(f.read_text(encoding="utf-8"))
        if title in existing:
            continue
        url = gh("issue", "create", "--title", title, "--body-file", str(f), "--label", f"stage:ready,tier:{tier}").stdout.strip()
        print(f"Card created: {title} → {url}")
        board_set(url, "ready")
    print("Done. On the board, set the view to “Board” and Column by “Stage” (once).")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup":
        setup()
    elif cmd == "move" and len(sys.argv) >= 4:
        print(move(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None))
    elif cmd == "next" and len(sys.argv) == 3:
        n = next_card(sys.argv[2])
        print(n or "")
    elif cmd == "report":
        print(report())
    else:
        print(__doc__)
        sys.exit(2)
