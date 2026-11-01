"""M10 review B1: the parser child cannot open sockets, run programs, read the library, or read the
API's environment through /proc."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.preview import hardening
from app.preview.sandbox import Limits, PreviewError, run_child, run_worker

API_ROOT = Path(__file__).resolve().parents[3] / "apps" / "api"
LIMITS = Limits(timeout=20.0, memory_bytes=1024**3, max_output=1 << 16)


def child(code: str, *argv: str) -> dict[str, object]:
    """Runs `code` in a fresh isolated interpreter after hardening.apply(); it prints JSON."""
    prelude = (
        f"import sys, json\nsys.path.insert(0, {str(API_ROOT)!r})\n"
        "from app.preview import hardening\nstatus = hardening.apply()\nout = {'status': status}\n"
    )
    program = prelude + textwrap.dedent(code) + "\nprint(json.dumps(out))\n"
    status, out = run_child([sys.executable, "-I", "-c", program, *argv], None, LIMITS)
    assert status == 0, out
    return json.loads(out)  # type: ignore[no-any-return]


def test_seccomp_blocks_sockets_exec_and_ptrace() -> None:
    out = child(
        """
        import socket, os
        def attempt(fn):
            try:
                fn()
                return "allowed"
            except PermissionError:
                return "denied"
            except OSError as e:
                return f"oserror:{e.errno}"
        out["inet"] = attempt(lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        out["inet6"] = attempt(lambda: socket.socket(socket.AF_INET6, socket.SOCK_STREAM))
        out["unix"] = attempt(lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
        out["exec"] = attempt(lambda: os.execv("/bin/true", ["true"]))
        """
    )
    status = out["status"]
    assert isinstance(status, dict)
    if not status["seccomp"]:
        pytest.skip("seccomp filters are not available in this environment")
    assert (out["inet"], out["inet6"], out["unix"], out["exec"]) == ("denied",) * 4


def test_landlock_hides_the_library_but_not_the_python_install(tmp_path: Path) -> None:
    secret = tmp_path / "library" / "alice" / "tax.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("confidential words")
    out = child(
        """
        import sys, json
        path = sys.argv[1]
        try:
            out["read"] = open(path).read()
        except PermissionError:
            out["read"] = "denied"
        import zipfile, json as _j, xml.parsers.expat  # the interpreter's own libraries still load
        out["stdlib"] = "ok"
        """,
        str(secret),
    )
    status = out["status"]
    assert isinstance(status, dict)
    if status["landlock"] != "enforced":
        pytest.skip(f"Landlock not available here ({status['landlock']})")
    assert out["read"] == "denied" and out["stdlib"] == "ok"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any /proc file")
def test_a_non_dumpable_api_hides_its_environment_from_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FILECANOPY_DATABASE_URL", "postgresql://u:hunter2@db/x")
    assert hardening.make_undumpable()
    try:
        code = (
            f"import os\ntry:\n open('/proc/{os.getpid()}/environ','rb').read()\n print('read')\n"
            "except PermissionError:\n print('denied')"
        )
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=20
        )
        assert proc.stdout.strip() == "denied"
    finally:
        import ctypes

        ctypes.CDLL(None).prctl(4, 1, 0, 0, 0)  # PR_SET_DUMPABLE back on for the test runner


def test_worker_refuses_to_parse_when_seccomp_cannot_be_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed: with the filter unavailable the worker answers SANDBOX_UNAVAILABLE and never
    touches the file. The environment variable stands in for a kernel without seccomp."""
    status, out = run_child(
        [
            sys.executable,
            "-I",
            "-c",
            f"import sys, runpy; sys.path.insert(0, {str(API_ROOT)!r});"
            "from app.preview import hardening; hardening.install_seccomp = lambda: False;"
            f"sys.argv = ['worker', 'extract', '{{\"ext\": \"pdf\", \"max_chars\": 10}}', "
            "'1073741824', '20']; "
            f"runpy.run_path({str(API_ROOT / 'app' / 'preview' / 'worker.py')!r}, "
            "run_name='__main__')",
        ],
        None,
        LIMITS,
    )
    assert status == 3 and json.loads(out) == {"error": "SANDBOX_UNAVAILABLE"}


def test_worker_status_probe_and_normal_operation(tmp_path: Path) -> None:
    with open(tmp_path / "x", "wb+") as empty:
        info = json.loads(run_worker("probe", None, LIMITS))
        assert set(info) == {"seccomp", "landlock"}
        if info["seccomp"]:
            # a real parse still works with everything switched on
            zip_path = tmp_path / "z.zip"
            import zipfile

            with zipfile.ZipFile(zip_path, "w") as z:
                z.writestr("a.txt", "a")
            with open(zip_path, "rb") as f:
                listing = json.loads(run_worker("zipdir", f, LIMITS, {"max_entries": 10}))
            assert listing["count"] == 1
        else:
            with pytest.raises(PreviewError) as err:
                run_worker("zipdir", empty, LIMITS, {"max_entries": 10})
            assert err.value.code == "SANDBOX_UNAVAILABLE"
