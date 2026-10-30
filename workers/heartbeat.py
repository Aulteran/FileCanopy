"""M01 worker process: shows the optional worker tier is alive via a Redis heartbeat.

Real job processing (Dramatiq, per ADR-001) arrives with the milestones that need it.
"""

import logging
import os
import signal
import socket
import threading
import time

import redis

from app.health import WORKER_HEARTBEAT_PREFIX
from app.logging_config import configure_logging

log = logging.getLogger("filecanopy.worker")
INTERVAL_SECONDS = 10
TTL_SECONDS = 30


def main() -> None:
    configure_logging()
    url = os.environ["FILECANOPY_REDIS_URL"]
    key = WORKER_HEARTBEAT_PREFIX + socket.gethostname()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    log.info("worker started")
    while not stop.is_set():
        try:
            client.set(key, str(int(time.time())), ex=TTL_SECONDS)
        except redis.RedisError as exc:
            log.warning("heartbeat failed: %s", type(exc).__name__)
        stop.wait(INTERVAL_SECONDS)
    log.info("worker stopping")


if __name__ == "__main__":
    main()
