"""Kernel-level limits for the parser child process and for the API process (M10 review B1).

All of it is best effort with the standard library (`ctypes`), Linux only:

- `make_undumpable()` (API process): children of the same user cannot read `/proc/<api>/environ`
  (the database URL) or its memory.
- `install_seccomp()` (parser child): no sockets, no `ptrace`/`process_vm_*`, no `exec`. A parser
  exploit cannot open a connection, so it cannot reach the database or the internet.
- `install_landlock()` (parser child): the child can read only the Python install and the app
  code, never `/library` or `/app-data`. The file to parse arrives on standard input.

`apply()` returns what was actually enforced, so callers can fail closed (seccomp) or record the
leftover risk (Landlock needs Linux 5.13+).
"""

import ctypes
import os
import platform
import struct
import sys
import sysconfig
from pathlib import Path

PR_SET_DUMPABLE = 4
PR_SET_SECCOMP = 22
PR_SET_NO_NEW_PRIVS = 38
SECCOMP_MODE_FILTER = 2

BPF_LD_W_ABS = 0x20
BPF_JEQ_K = 0x15
BPF_RET_K = 0x06
RET_ALLOW = 0x7FFF0000
RET_ERRNO_EPERM = 0x00050000 | 1
RET_KILL_PROCESS = 0x80000000

ARCHES = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}
# socket, socketpair, connect, bind, execve, execveat, ptrace, process_vm_readv, process_vm_writev
DENIED = {
    "x86_64": (41, 53, 42, 49, 59, 322, 101, 310, 311),
    "aarch64": (198, 199, 203, 200, 221, 281, 117, 270, 271),
}

NR_LANDLOCK_CREATE, NR_LANDLOCK_ADD, NR_LANDLOCK_RESTRICT = 444, 445, 446
LANDLOCK_RULE_PATH_BENEATH = 1
FS_EXECUTE, FS_READ_FILE, FS_READ_DIR = 1 << 0, 1 << 2, 1 << 3
ALL_FS_V1 = (1 << 13) - 1


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)  # the running process's own libc: no file lookups


def make_undumpable() -> bool:
    """Marks this process non-dumpable. Returns False when the call is unavailable."""
    try:
        return bool(_libc().prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0)
    except (OSError, AttributeError):
        return False


def _no_new_privs(libc: ctypes.CDLL) -> bool:
    return bool(libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0)


def install_seccomp() -> bool:
    machine = platform.machine()
    if machine not in ARCHES or sys.platform != "linux":
        return False
    try:
        libc = _libc()
        denied = DENIED[machine]
        prog = [
            (BPF_LD_W_ABS, 0, 0, 4),  # architecture
            (BPF_JEQ_K, 1, 0, ARCHES[machine]),
            (BPF_RET_K, 0, 0, RET_KILL_PROCESS),  # another ABI (for example 32-bit): never
            (BPF_LD_W_ABS, 0, 0, 0),  # system call number
        ]
        for i, nr in enumerate(denied):
            prog.append((BPF_JEQ_K, len(denied) - i, 0, nr))
        prog.append((BPF_RET_K, 0, 0, RET_ALLOW))
        prog.append((BPF_RET_K, 0, 0, RET_ERRNO_EPERM))
        raw = b"".join(struct.pack("=HBBI", c, jt, jf, k) for c, jt, jf, k in prog)
        buf = ctypes.create_string_buffer(raw, len(raw))

        class Fprog(ctypes.Structure):
            _fields_ = (("len", ctypes.c_ushort), ("filter", ctypes.c_void_p))

        fprog = Fprog(len(prog), ctypes.cast(buf, ctypes.c_void_p))
        if not _no_new_privs(libc):
            return False
        return bool(libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(fprog), 0, 0) == 0)
    except (OSError, AttributeError, ValueError):
        return False


def readable_roots() -> list[str]:
    """What the parser child may read: the interpreter, its libraries and the app code."""
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, "/usr", "/lib", "/lib64", "/bin"}
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        value = sysconfig.get_paths().get(key)
        if value:
            roots.add(value)
    roots.add(str(Path(__file__).resolve().parents[2]))  # apps/api: the app package
    return sorted(r for r in roots if os.path.isdir(r))


def install_landlock(roots: list[str] | None = None) -> str:
    """'enforced' when the file-system restriction is active, otherwise a short reason."""
    if sys.platform != "linux":
        return "unsupported_os"
    libc = _libc()
    try:
        attr = ctypes.create_string_buffer(struct.pack("=Q", ALL_FS_V1), 8)
        ruleset = libc.syscall(NR_LANDLOCK_CREATE, attr, 8, 0)
        if ruleset < 0:
            return "unavailable"
        try:
            for path in roots if roots is not None else readable_roots():
                fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
                try:
                    rule = ctypes.create_string_buffer(
                        struct.pack("=Qi", FS_EXECUTE | FS_READ_FILE | FS_READ_DIR, fd), 12
                    )
                    if libc.syscall(NR_LANDLOCK_ADD, ruleset, LANDLOCK_RULE_PATH_BENEATH, rule, 0):
                        return "rule_failed"
                finally:
                    os.close(fd)
            if not _no_new_privs(libc):
                return "no_new_privs_failed"
            if libc.syscall(NR_LANDLOCK_RESTRICT, ruleset, 0) != 0:
                return "restrict_failed"
            return "enforced"
        finally:
            os.close(ruleset)
    except (OSError, AttributeError, ValueError):
        return "unavailable"


def apply() -> dict[str, object]:
    """Applies both restrictions in the parser child. Landlock first (it needs the file system),
    then seccomp (which forbids the calls Landlock setup uses)."""
    landlock = install_landlock()
    return {"seccomp": install_seccomp(), "landlock": landlock}
