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
BPF_JGE_K = 0x35
BPF_JSET_K = 0x45
RET_ERRNO_ENOSYS = 0x00050000 | 38
X32_BIT = 0x40000000
# Flags that would let open/openat write: O_WRONLY | O_RDWR | O_CREAT | O_TRUNC.
OPEN_WRITE_FLAGS = 0o3 | 0o100 | 0o1000

# Denied with EPERM. Network and process control (B1), anything that changes or destroys files
# (B1a), io_uring (it can do socket/connect/openat/unlinkat without passing through seccomp, B1b)
# and pidfd_getfd (steals a descriptor of a sibling, B1c).
_COMMON = {
    "io_uring": (425, 426, 427),
    "pidfd_getfd": (438,),
    "fchmodat2": (452,),
}
DENIED = {
    "x86_64": (
        41,
        53,
        42,
        49,  # socket, socketpair, connect, bind
        59,
        322,  # execve, execveat
        101,
        310,
        311,  # ptrace, process_vm_readv/writev
        76,
        77,  # truncate, ftruncate
        82,
        83,
        84,
        85,
        86,
        87,
        88,  # rename, mkdir, rmdir, creat, link, unlink, symlink
        90,
        91,
        92,
        93,
        94,  # chmod, fchmod, chown, fchown, lchown
        133,  # mknod
        258,
        259,
        260,
        263,
        264,
        265,
        266,
        268,
        316,  # *at variants incl. renameat2
        *(n for v in _COMMON.values() for n in v),
    ),
    "aarch64": (
        198,
        199,
        203,
        200,
        221,
        281,
        117,
        270,
        271,
        45,
        46,  # truncate, ftruncate
        33,
        34,
        35,
        36,
        37,
        38,
        276,  # mknodat, mkdirat, unlinkat, symlinkat, linkat, renameat(2)
        52,
        53,
        54,
        55,  # fchmod, fchmodat, fchownat, fchown
        *(n for v in _COMMON.values() for n in v),
    ),
}
# (number, argument index of the flags) for the calls checked rather than denied.
OPEN_CALLS = {"x86_64": ((2, 1), (257, 2)), "aarch64": ((56, 2),)}
OPENAT2 = 437

NR_LANDLOCK_CREATE, NR_LANDLOCK_ADD, NR_LANDLOCK_RESTRICT = 444, 445, 446
LANDLOCK_RULE_PATH_BENEATH = 1
FS_EXECUTE, FS_READ_FILE, FS_READ_DIR = 1 << 0, 1 << 2, 1 << 3
ALL_FS_V1 = (1 << 13) - 1
FS_REFER, FS_TRUNCATE = 1 << 13, 1 << 14
LANDLOCK_CREATE_RULESET_VERSION = 1


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


def build_filter(machine: str) -> list[tuple[int, int, int, int]]:
    """The seccomp program as (code, jt, jf, k). Jumps are written with labels and resolved here."""
    ins: list[tuple[int, object, object, int]] = []
    labels: dict[str, int] = {}

    def mark(name: str) -> None:
        labels[name] = len(ins)

    def add(code: int, jt: object, jf: object, k: int) -> None:
        ins.append((code, jt, jf, k))

    add(BPF_LD_W_ABS, 0, 0, 4)  # architecture
    add(BPF_JEQ_K, 0, "kill", ARCHES[machine])  # another ABI (for example 32-bit): never
    add(BPF_LD_W_ABS, 0, 0, 0)  # system call number
    add(BPF_JGE_K, "kill", 0, X32_BIT)  # x32 numbers would skip every check below
    for nr in DENIED[machine]:
        add(BPF_JEQ_K, "eperm", 0, nr)
    add(BPF_JEQ_K, "enosys", 0, OPENAT2)  # flags are in a struct: cannot be checked, so not offered
    for i, (nr, _arg) in enumerate(OPEN_CALLS[machine]):
        add(BPF_JEQ_K, f"open{i}", 0, nr)
    add(BPF_RET_K, 0, 0, RET_ALLOW)
    for i, (_nr, arg) in enumerate(OPEN_CALLS[machine]):
        mark(f"open{i}")
        add(BPF_LD_W_ABS, 0, 0, 16 + 8 * arg)  # low word of the flags (little endian)
        add(BPF_JSET_K, "eperm", "allow", OPEN_WRITE_FLAGS)
    mark("allow")
    add(BPF_RET_K, 0, 0, RET_ALLOW)
    mark("eperm")
    add(BPF_RET_K, 0, 0, RET_ERRNO_EPERM)
    mark("enosys")
    add(BPF_RET_K, 0, 0, RET_ERRNO_ENOSYS)
    mark("kill")
    add(BPF_RET_K, 0, 0, RET_KILL_PROCESS)

    def offset(target: object, at: int) -> int:
        return (labels[target] - at - 1) if isinstance(target, str) else int(target)  # type: ignore[call-overload]

    return [(c, offset(t, i), offset(f, i), k) for i, (c, t, f, k) in enumerate(ins)]


def install_seccomp() -> bool:
    machine = platform.machine()
    if machine not in ARCHES or sys.platform != "linux":
        return False
    try:
        libc = _libc()
        prog = build_filter(machine)
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
        abi = libc.syscall(NR_LANDLOCK_CREATE, None, 0, LANDLOCK_CREATE_RULESET_VERSION)
        handled = ALL_FS_V1
        if abi >= 2:
            handled |= FS_REFER
        if abi >= 3:
            handled |= FS_TRUNCATE  # ABI 1 does not cover truncate(2); seccomp does as well
        attr = ctypes.create_string_buffer(struct.pack("=Q", handled), 8)
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
