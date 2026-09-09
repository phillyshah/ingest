"""Long-running worker: claims jobs, runs stages, heartbeats, reclaims expired leases. Low concurrency by default."""

from __future__ import annotations

import logging
import os
import signal
import socket
import time

from moveai_db import connect

from . import queue as q
from .pipeline import run_job

log = logging.getLogger("moveai.worker")
_stop = False


def _handle(sig, frame):  # noqa: ANN001
    global _stop
    _stop = True


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    poll = float(os.environ.get("WORKER_POLL_SECONDS", "2"))
    log.info("worker %s starting", worker_id)
    with connect() as conn:
        last_reclaim = 0.0
        while not _stop:
            if time.monotonic() - last_reclaim > 30:
                n = q.reclaim_expired(conn)
                conn.commit()
                if n:
                    log.warning("reclaimed %d expired job leases", n)
                last_reclaim = time.monotonic()
            job = q.claim(conn, worker_id)
            conn.commit()
            if not job:
                time.sleep(poll)
                continue
            state = run_job(conn, job, commit=True)
            log.info("job %s stage=%s -> %s", job["id"], job["stage"], state)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
