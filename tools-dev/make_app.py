"""Builds Reach.app: a regular Mac app that runs the Reach engine (app/) in the background and opens it in
the browser.

    python3 tools-dev/make_app.py            -> dist/Reach.app and dist/Reach-app.zip
"""
import os
import plistlib
import shutil
import stat
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP = DIST / "Reach.app"
VERSION = "0.1.2"

LAUNCHER = r'''#!/bin/bash
# Reach.app launcher: opens the running engine if it is this version, otherwise stops the old one and starts this one.
SUPPORT="$HOME/Library/Application Support/Reach"
mkdir -p "$SUPPORT"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') launcher: $*" >> "$SUPPORT/reach.log"; }
# On Apple silicon, macOS can start script-based apps in Intel emulation (Rosetta); restart natively if so.
if [ "$(/usr/sbin/sysctl -n sysctl.proc_translated 2>/dev/null)" = "1" ]; then
  log "started in Intel emulation; restarting natively"
  exec /usr/bin/arch -arm64 /bin/bash "$0" "$@"
fi
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
VERSION="__VERSION__"
log "opening Reach $VERSION from $RES"
case "$RES" in
  */AppTranslocation/*)
    # macOS is running a hidden temporary copy (it does this for downloaded apps). If the approved app is in
    # Applications, clear its "downloaded" flag once and reopen it from there.
    REAL="/Applications/Reach.app"
    if [ -d "$REAL" ] && /usr/bin/xattr -p com.apple.quarantine "$REAL" >/dev/null 2>&1; then
      /usr/bin/xattr -dr com.apple.quarantine "$REAL" 2>/dev/null
      if ! /usr/bin/xattr -p com.apple.quarantine "$REAL" >/dev/null 2>&1; then
        log "was running from a temporary copy; reopening $REAL"
        /usr/bin/open "$REAL"
        exit 0
      fi
    fi
    log "running from a temporary copy; move Reach into Applications with Finder to stop this"
    ;;
esac
if [ -f "$SUPPORT/port" ]; then
  PORT="$(cat "$SUPPORT/port")"
  PING="$(/usr/bin/curl -fs --max-time 3 "http://127.0.0.1:$PORT/ping")"
  RUNVER="$(echo "$PING" | cut -d' ' -f2)"
  RUNDIR="$(echo "$PING" | cut -d' ' -f3-)"
  case "$PING" in
    reach*)
      # same version and its files still exist: just show it (macOS may run apps from a changing hidden copy,
      # so the folder is not compared, only checked)
      if [ "$RUNVER" = "$VERSION" ] && [ -f "$RUNDIR/server.py" ]; then
        log "engine $RUNVER already running on port $PORT; opening it"
        /usr/bin/open "http://127.0.0.1:$PORT/"
        exit 0
      fi
      log "stopping older engine ($PING)"
      if [ -f "$SUPPORT/control" ]; then
        /usr/bin/curl -fs --max-time 3 -X POST -H "X-Reach-Token: $(cat "$SUPPORT/control")" "http://127.0.0.1:$PORT/api/quit" >/dev/null 2>&1
        sleep 1
      fi
      /usr/bin/pkill -f "Reach.app/Contents/Resources/engine/server.py" >/dev/null 2>&1
      /usr/bin/pkill -f "Application Support/Reach/engine-" >/dev/null 2>&1
      sleep 1
      rm -f "$SUPPORT/port"
      ;;
    *)
      log "no engine answering on port $PORT"
      rm -f "$SUPPORT/port"
      ;;
  esac
fi
if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
  log "developer tools missing; asking to install"
  /usr/bin/osascript -e 'display dialog "Reach needs Apple’s free developer tools (they include Python).

Click Install in the next window, wait until it finishes, then open Reach again." buttons {"OK"} default button 1 with title "Reach"' >/dev/null 2>&1
  /usr/bin/xcode-select --install >/dev/null 2>&1
  exit 0
fi
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# run from a copy in Application Support: macOS may delete the temporary app copy it launched us from
ENGINE="$SUPPORT/engine-$VERSION"
if [ ! -f "$ENGINE/.complete" ]; then
  rm -rf "$ENGINE"
  /bin/cp -R "$RES/engine" "$ENGINE" && touch "$ENGINE/.complete"
fi
for OLD in "$SUPPORT"/engine-*; do
  case "$OLD" in *"-$VERSION") ;; *) [ -d "$OLD" ] && rm -rf "$OLD" ;; esac
done
log "starting engine $VERSION ($(uname -m))"
nohup /usr/bin/python3 "$ENGINE/server.py" --open --version "$VERSION" >> "$SUPPORT/reach.log" 2>&1 &
exit 0
'''



def make_icon(path):
    from PIL import Image, ImageDraw
    S = 1024
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = 100                                   # macOS icon grid: 824 px tile inside 1024
    d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=185, fill=(0, 78, 133, 255))
    w, c = 64, S // 2
    white = (255, 255, 255, 255)
    d.arc([c - 210, 260, c + 210, 680], 180, 360, fill=white, width=w)        # doorway arch
    d.line([(c - 210 + w // 2 - 2, 470), (c - 210 + w // 2 - 2, 760)], fill=white, width=w)
    d.line([(c + 210 - w // 2 + 2, 470), (c + 210 - w // 2 + 2, 760)], fill=white, width=w)
    d.rectangle([c - 70, 560, c + 70, 760], fill=white)                       # open door
    img.save(path, format="ICNS")


def main():
    if APP.exists():
        shutil.rmtree(APP)
    contents = APP / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    res = contents / "Resources"
    res.mkdir()
    plist = {
        "CFBundleName": "Reach", "CFBundleDisplayName": "Reach", "CFBundleIdentifier": "app.reach.local",
        "CFBundleExecutable": "Reach", "CFBundleIconFile": "AppIcon", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION, "LSMinimumSystemVersion": "11.0",
        "LSUIElement": True, "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "Runs on this Mac only. Opens in your browser.",
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(plist))
    launcher = contents / "MacOS" / "Reach"
    launcher.write_text(LAUNCHER.replace("__VERSION__", VERSION))
    launcher.chmod(0o755)
    make_icon(res / "AppIcon.icns")
    shutil.copytree(ROOT / "app", res / "engine", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    zpath = DIST / "Reach-app.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(APP.rglob("*")):
            info = zipfile.ZipInfo(str(f.relative_to(DIST)), date_time=time.localtime(f.stat().st_mtime)[:6])
            mode = f.stat().st_mode
            info.external_attr = (mode & 0xFFFF) << 16
            info.create_system = 3                      # Unix, so Finder keeps the executable bit
            if f.is_dir():
                info.filename += "/"
                info.external_attr |= 0x10
                z.writestr(info, b"")
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, f.read_bytes())
    print(f"Built {APP.relative_to(ROOT)} and {zpath.relative_to(ROOT)} ({zpath.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
