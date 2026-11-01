"""Runs the parsers for untrusted bytes in a short-lived, resource-limited child process.

The child gets the file on standard input only (it needs no path and no file-system access), an
empty environment (so no database URL or secret), a memory cap, a CPU cap, a ban on writing
files, a wall-clock timeout that kills its whole process group, and an output cap. A crash,
timeout or garbage becomes an error code; the caller never sees an exception from the parser.

This is process isolation and resource limits, not a container: the child runs as the API's user.
The Office converter is the part that is sealed in a container (ADR-019 section 5).
"""

import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

WORKER = Path(__file__).with_name("worker.py")
EXIT_ERROR = 3  # the child reports a coded error as {"error": "CODE"} on stdout and exits 3


class PreviewError(Exception):
    """A coded refusal. `code` is shown to the person; `status` is the HTTP status."""

    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code, self.status = code, status


class Gate:
    """At most N parser children at once; a request that cannot get in soon says it is busy."""

    def __init__(self, limit: int, wait_seconds: float = 10.0) -> None:
        self._sem = threading.BoundedSemaphore(limit)
        self._wait = wait_seconds

    def __enter__(self) -> "Gate":
        if not self._sem.acquire(timeout=self._wait):
            raise PreviewError("PREVIEW_BUSY", 503)
        return self

    def __exit__(self, *_: object) -> None:
        self._sem.release()


@dataclass(frozen=True)
class Limits:
    timeout: float
    memory_bytes: int
    max_output: int
    require_seccomp: bool = True


def run_child(
    argv: Sequence[str],
    source: BinaryIO | None,
    limits: Limits,
    *,
    env: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    """Runs `argv` with `source` as standard input. Returns (exit code, stdout). Raises
    PreviewError("TIMEOUT") when the deadline passes and PreviewError("OUTPUT_TOO_LARGE")
    when the child writes more than allowed. The whole process group is killed in both cases."""
    stdin: int | BinaryIO | None = subprocess.DEVNULL
    if source is not None:
        source.seek(0)
        stdin = source
    proc = subprocess.Popen(  # noqa: S603 - argv is built from constants and our own paths
        list(argv),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env if env is not None else {"LANG": "C.UTF-8"},
        cwd="/",
        close_fds=True,
        start_new_session=True,
    )
    assert proc.stdout is not None  # noqa: S101 - PIPE was requested above
    out = bytearray()
    deadline = time.monotonic() + limits.timeout
    fd = proc.stdout.fileno()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PreviewError("TIMEOUT", 504)
            ready, _, _ = select.select([fd], [], [], min(remaining, 0.5))
            if not ready:
                continue
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            out += chunk
            if len(out) > limits.max_output:
                raise PreviewError("OUTPUT_TOO_LARGE", 422)
        remaining = max(deadline - time.monotonic(), 0.1)
        try:
            code = proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise PreviewError("TIMEOUT", 504) from exc
    except BaseException:
        _kill(proc)
        raise
    finally:
        proc.stdout.close()
    return code, bytes(out)


def _kill(proc: "subprocess.Popen[bytes]") -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_worker(
    op: str, source: BinaryIO | None, limits: Limits, args: dict[str, Any] | None = None
) -> bytes:
    """One worker operation on an open file. Returns the worker's stdout (JSON, or image bytes
    for `thumb`). Any failure is a PreviewError with a code."""
    argv = [
        sys.executable,
        "-I",
        str(WORKER),
        op,
        json.dumps({**(args or {}), "require_seccomp": limits.require_seccomp}),
        str(limits.memory_bytes),
        str(int(limits.timeout) + 2),
    ]
    code, out = run_child(argv, source, limits)
    if code == 0:
        return out
    if code == EXIT_ERROR:
        try:
            err = json.loads(out.decode("utf-8", "replace")).get("error")
        except ValueError:
            err = None
        if isinstance(err, str) and err.isidentifier() and err.upper() == err:
            raise PreviewError(err, 503 if err == "SANDBOX_UNAVAILABLE" else 422)
    raise PreviewError("PREVIEW_FAILED")  # crashed, killed by a limit, or spoke nonsense
