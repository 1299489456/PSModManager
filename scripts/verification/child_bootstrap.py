"""Private, stdin-gated job child. Never spawn before the owner assigns its job."""
import json
import subprocess
import sys
import traceback
from pathlib import Path


def main():
    request = json.loads(sys.stdin.readline())
    status = Path(request["status_path"])
    try:
        child = subprocess.Popen(request["argv"], stdin=subprocess.DEVNULL)
    except Exception as exc:
        status.write_text(json.dumps({"started": False, "error": str(exc)}), encoding="utf-8")
        traceback.print_exc()
        return 127
    status.write_text(json.dumps({"started": True, "pid": child.pid}), encoding="utf-8")
    return child.wait()


if __name__ == "__main__":
    sys.exit(main())
