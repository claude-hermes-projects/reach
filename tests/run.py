"""Runs Reach's tests, each file in its own process with a fresh stand-in Cloudflare.

    python3 tests/run.py                      every test file
    python3 tests/run.py tests/test_engine.py one or more files
Exit code 0 only when every file passes.
"""
import glob, os, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(here)
pending_file = os.path.join(root, "cards", "pending.txt")
pending = set()
if os.path.exists(pending_file):
    pending = {os.path.normpath(os.path.join(root, l.strip())) for l in open(pending_file) if l.strip() and not l.startswith("#")}
if sys.argv[1:]:
    files = [os.path.abspath(f) for f in sys.argv[1:]]          # named files always run, pending or not
else:
    files = [f for f in sorted(glob.glob(os.path.join(here, "test_*.py"))) if os.path.normpath(f) not in pending]
    for f in sorted(pending):
        print(f"SKIP  {os.path.basename(f)}  (card not done yet; see cards/pending.txt)")
failed = []
for f in files:
    t = time.time()
    r = subprocess.run([sys.executable, f], capture_output=True, text=True, timeout=600)
    lines = (r.stdout + r.stderr).strip().splitlines()
    bad = [l for l in lines if l.startswith("FAIL") or "Traceback" in l or "Error" in l.split(":")[0]]
    status = "PASS" if r.returncode == 0 else "FAIL"
    print(f"{status}  {os.path.basename(f)}  ({time.time() - t:.0f}s)")
    if r.returncode != 0:
        failed.append(f)
        for l in (bad or lines[-15:])[:15]:
            print("      " + l)
        if r.returncode != 1:
            print("\n".join("      " + l for l in lines[-12:]))
print(f"\n{len(files) - len(failed)} of {len(files)} test files passed")
sys.exit(1 if failed else 0)
