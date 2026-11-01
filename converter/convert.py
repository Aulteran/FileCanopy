"""Runs LibreOffice headless on one file and returns a PDF. Standard library only.

Each conversion gets a fresh work directory and a fresh LibreOffice profile that pins macro
security to its highest level and turns active content and link updates off. The process runs in
its own session so a timeout kills everything it started, with CPU and file-size limits.
"""

import os
import resource
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

FILTERS = {
    "docx": "writer_pdf_Export",
    "doc": "writer_pdf_Export",
    "odt": "writer_pdf_Export",
    "xlsx": "calc_pdf_Export",
    "xls": "calc_pdf_Export",
    "ods": "calc_pdf_Export",
    "pptx": "impress_pdf_Export",
    "ppt": "impress_pdf_Export",
    "odp": "impress_pdf_Export",
}
MAX_PDF_BYTES = 256 * 1024 * 1024

REGISTRY = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry"
 xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="OfficeBasic" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>
<item oor:path="/org.openoffice.Office.Writer/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>2</value></prop></item>
<item oor:path="/org.openoffice.Office.Common/Misc"><prop oor:name="FirstRun" oor:op="fuse"><value>false</value></prop></item>
</oor:items>
"""


class ConvertError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _limits(cpu_seconds: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_PDF_BYTES * 2, MAX_PDF_BYTES * 2))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 1024))


def convert(
    data_path: Path,
    ext: str,
    out_pdf: Path,
    *,
    timeout: int,
    max_pages: int,
    soffice: str = "soffice",
    scratch: str | None = None,
) -> None:
    """Converts `data_path` (a file of type `ext`) to `out_pdf`. Raises ConvertError."""
    flt = FILTERS.get(ext)
    if flt is None:
        raise ConvertError("UNSUPPORTED")
    work = Path(tempfile.mkdtemp(prefix="conv-", dir=scratch))
    try:
        os.chmod(work, 0o700)
        profile = work / "profile"
        (profile / "user").mkdir(parents=True)
        (profile / "user" / "registrymodifications.xcu").write_text(REGISTRY, encoding="utf-8")
        src = work / f"input.{ext}"
        shutil.copyfile(data_path, src)
        outdir = work / "out"
        outdir.mkdir()
        # one page more than allowed, so the API can tell "exactly at the limit" from "over"
        options = f'{{"PageRange":{{"type":"string","value":"1-{max_pages + 1}"}}}}'
        argv = [
            soffice,
            "--headless",
            "--invisible",
            "--norestore",
            "--nolockcheck",
            "--nodefault",
            "--nologo",
            "--nofirststartwizard",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to",
            f"pdf:{flt}:{options}",
            "--outdir",
            str(outdir),
            str(src),
        ]
        env = {"HOME": str(work), "TMPDIR": str(work), "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"}
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, file name is ours
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=work,
            env=env,
            start_new_session=True,
            preexec_fn=lambda: _limits(timeout + 30),  # noqa: PLW1509
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ConvertError("TIMEOUT") from None
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # nothing the document started may linger
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()
        produced = outdir / "input.pdf"
        if proc.returncode != 0 or not produced.is_file():
            raise ConvertError("CONVERT_FAILED")
        size = produced.stat().st_size
        if size == 0 or size > MAX_PDF_BYTES:
            raise ConvertError("TOO_LARGE" if size else "CONVERT_FAILED")
        with open(produced, "rb") as f:
            if not f.read(1024).lstrip().startswith(b"%PDF-"):
                raise ConvertError("CONVERT_FAILED")
        shutil.move(produced, out_pdf)
    finally:
        shutil.rmtree(work, ignore_errors=True)
