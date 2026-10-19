"""Frozen stdin-gated metadata console host; no desktop or legacy startup."""
from backend.scanning.worker_host import main

if __name__ == '__main__':
    raise SystemExit(main())
