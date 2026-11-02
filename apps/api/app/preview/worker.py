"""Child-process entry point for parsing untrusted files (see `sandbox`).

    python -I worker.py OP JSON_ARGS MEMORY_BYTES CPU_SECONDS   < the file

The file arrives on standard input; the result goes to standard output. Limits are applied before
anything is imported or read. Exit 0 = result, exit 3 = `{"error": CODE}`, anything else = crash.
"""

import json
import resource
import sys
from pathlib import Path
from typing import BinaryIO

sys.dont_write_bytecode = True


def _limit(memory: int, cpu: int) -> None:
    for which, value in (
        (resource.RLIMIT_AS, memory),
        (resource.RLIMIT_CPU, cpu),
        (resource.RLIMIT_FSIZE, 0),  # a parser has no business writing files
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(which, (value, value))


def main() -> int:
    op, raw_args, memory, cpu = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    _limit(memory, cpu)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    args = json.loads(raw_args)
    # Kernel limits before any parser code is imported or any byte is read (review B1): no sockets
    # or exec (seccomp), and no reading of the library or app data (Landlock, where available).
    from app.preview import hardening

    status = hardening.apply()
    hardening.make_undumpable()  # execve made the child dumpable again (hide /proc/<pid>)
    if op == "probe":
        sys.stdout.write(json.dumps(status))
        return 0
    if args.get("require_seccomp", True) and not status["seccomp"]:
        sys.stdout.write(json.dumps({"error": "SANDBOX_UNAVAILABLE"}))
        return 3
    from app.preview import archive, extract, pdfsplit, thumb

    stdin = open(0, "rb", closefd=False)  # noqa: SIM115 - fd 0 stays open; this is the file
    try:
        if op == "extract":
            fn = extract.pdf_text if args["ext"] == "pdf" else extract.docx_text
            result: object = fn(stdin, int(args["max_chars"]))
        elif op == "thumb":
            sys.stdout.buffer.write(
                thumb.make_thumbnail(stdin, int(args["edge"]), int(args["max_pixels"]))
            )
            return 0
        elif op == "zipdir":
            result = archive.list_zip(stdin, int(args["max_entries"]))
        elif op == "pdfpages":
            result = pdfsplit.page_descriptors(
                stdin, int(args["max_pages"]), int(args["head_chars"])
            )
        elif op == "pdfpart":
            sys.stdout.buffer.write(pdfsplit.extract_part(stdin, [int(p) for p in args["pages"]]))
            return 0
        elif op == "pdfinfo":
            result = pdfinfo(stdin, int(args["max_pages"]))
        else:
            return 2
    except extract.Refuse as refusal:
        sys.stdout.write(json.dumps({"error": refusal.code}))
        return 3
    sys.stdout.write(json.dumps(result))
    return 0


def pdfinfo(f: BinaryIO, max_pages: int) -> dict[str, int]:
    """Checks a PDF the converter produced: it is a PDF, it opens and it is not too long."""
    from app.preview.extract import Refuse

    head = f.read(1024)
    f.seek(0)
    if b"%PDF-" not in head:
        raise Refuse("CONVERT_FAILED")
    from pypdf import PdfReader

    try:
        pages = len(PdfReader(f, strict=False).pages)
    except Exception:  # noqa: BLE001 - any parse failure means the conversion is unusable
        raise Refuse("CONVERT_FAILED") from None
    if pages > max_pages:
        raise Refuse("TOO_MANY_PAGES")
    return {"pages": pages}


if __name__ == "__main__":
    sys.exit(main())
