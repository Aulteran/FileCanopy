"""Command line for backups, usable without the web app (ADR-015 section 5):

    python -m app.backup.cli list   <backups folder>
    python -m app.backup.cli verify <archive> [<archive> ...] [--key-file FILE]
    python -m app.backup.cli decrypt <archive.fcb> <out.tar.gz> --key-file FILE

The recovery key is read from `--key-file` or the FILECANOPY_RECOVERY_KEY environment variable,
never from an argument (arguments are visible to other users of the machine). Without a key,
`verify` checks an encrypted archive against the checksum in its index file only. Output carries
file names of the archives and error classes, never file names or content from inside them.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from app.backup import crypto
from app.backup.engine import verify_archive


def _key(args: argparse.Namespace) -> str | None:
    if args.key_file:
        return Path(args.key_file).read_text(encoding="utf-8").strip()
    return os.environ.get("FILECANOPY_RECOVERY_KEY") or None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def verify_one(path: Path, key: str | None) -> str | None:
    """Returns None when the archive is sound, otherwise a short reason."""
    sidecar = path.with_name(path.name + ".json")
    if sidecar.is_file():
        info = json.loads(sidecar.read_text(encoding="utf-8"))
        if path.stat().st_size != info.get("size_bytes") or _sha256(path) != info.get("sha256"):
            return "checksum differs from the index file"
    with open(path, "rb") as handle:
        encrypted = crypto.looks_encrypted(handle.read(4))
    if encrypted and key is None:
        return None  # structure cannot be read without the key; the checksum above passed
    try:
        verify_archive(path, None, None, None, recovery_key=key if encrypted else None)
    except crypto.CryptoError as exc:
        return type(exc).__name__
    except Exception as exc:  # damaged gzip or tar, or a member that fails its checksum
        return type(exc).__name__
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.backup.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p_list = sub.add_parser("list")
    p_list.add_argument("folder")
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("archives", nargs="+")
    p_verify.add_argument("--key-file")
    p_decrypt = sub.add_parser("decrypt")
    p_decrypt.add_argument("archive")
    p_decrypt.add_argument("output")
    p_decrypt.add_argument("--key-file")
    args = parser.parse_args(argv)

    if args.command == "list":
        for sidecar in sorted(Path(args.folder).rglob("*.json")):
            info = json.loads(sidecar.read_text(encoding="utf-8"))
            print(  # noqa: T201
                info.get("chain_id"),
                f"{info.get('seq'):04d}",
                info.get("kind"),
                info.get("created_at"),
                info.get("size_bytes"),
                "encrypted" if info.get("encrypted") else "plain",
            )
        return 0

    key = _key(args) if hasattr(args, "key_file") else None
    if args.command == "verify":
        failed = 0
        for name in args.archives:
            reason = verify_one(Path(name), key)
            print(
                f"{'OK  ' if reason is None else 'FAIL'} {Path(name).name}"
                + (f": {reason}" if reason else "")
            )  # noqa: T201
            failed += reason is not None
        return 1 if failed else 0

    if key is None:
        print("a recovery key is required (--key-file or FILECANOPY_RECOVERY_KEY)", file=sys.stderr)  # noqa: T201
        return 2
    out = Path(args.output)
    if out.exists():
        print("output file exists; refusing to overwrite", file=sys.stderr)  # noqa: T201
        return 2
    try:
        with open(args.archive, "rb") as src, open(out, "xb") as dst:
            reader = crypto.DecryptingReader(src, recovery_key=key)
            while block := reader.read(1024 * 1024):
                dst.write(block)
    except crypto.CryptoError as exc:
        out.unlink(missing_ok=True)
        print(f"decryption failed: {type(exc).__name__}", file=sys.stderr)  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
