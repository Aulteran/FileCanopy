"""Remote backup destinations (M27, ADR-021): SFTP and S3-compatible object storage.

A job with a remote destination still writes its archive to the local backup volume first (the
staging copy). The engine then uploads it here, the upload is verified, and only then is the run
recorded as `ok`: a partial remote object is never a restore point. Uploads are atomic (SFTP:
temporary name then rename; S3: an object only exists once the upload completed), sizes and
SHA-256 are checked afterwards.

Security: the host key of an SFTP server is pinned by the administrator (never trusted on first
use); TLS verification of S3 endpoints is on; hosts resolving to loopback, private, link-local or
metadata addresses are refused unless the operator allows them. Credentials are opened by the
caller, held only for the connection and never put in a log line, exception text or audit row:
every error leaves this module as a `DestinationError` with a fixed code.
"""

import base64
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import socket
from io import StringIO
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.backup.jobs import JobError, JobSpec, open_credentials
from app.config import Settings

log = logging.getLogger("filecanopy.backup")

CONNECT_TIMEOUT = 20.0


class DestinationError(Exception):
    """A remote operation failed. `code` is fixed text; secrets never reach the message."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail  # public facts only (for example the host key the server presented)


class Destination(Protocol):
    def test(self) -> None: ...
    def put(self, local: Path, key: str, sha256: str, size: int) -> None: ...
    def get(self, key: str, local: Path) -> None: ...
    def delete(self, key: str) -> None: ...
    def close(self) -> None: ...


# ------------------------------------------------------------------ address policy


_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _blocked(address: str) -> bool:
    """Only globally routable addresses pass (an allow-list: CGNAT 100.64.0.0/10, the ranges
    used by VPN overlays and every private, loopback or link-local range are refused). Addresses
    that embed an IPv4 one (mapped, 6to4, NAT64) are judged by the address inside."""
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return not ip.is_global


def resolve_checked(host: str, port: int, *, allow_private: bool) -> str:
    """The address to connect to, after the policy check. The caller connects to this address,
    so a name that changes between the check and the connection cannot slip through."""
    try:
        infos = socket.getaddrinfo(host.strip("[]"), port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise DestinationError("DESTINATION_UNREACHABLE") from exc
    addresses = [str(info[4][0]) for info in infos]
    if not addresses:
        raise DestinationError("DESTINATION_UNREACHABLE")
    if not allow_private and any(_blocked(a) for a in addresses):
        raise DestinationError("DESTINATION_ADDRESS_BLOCKED")
    return addresses[0]


def host_fingerprint(key_bytes: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key_bytes).digest()).decode().rstrip("=")


def _join(*parts: str) -> str:
    return "/".join(p.strip("/") for p in parts if p and p.strip("/"))


# ------------------------------------------------------------------ SFTP


class SftpDestination:
    def __init__(self, spec: JobSpec, creds: dict[str, str], *, allow_private: bool) -> None:
        self.base = str(spec.destination.get("path", ""))
        self._sftp: Any = None
        self._transport: Any = None
        self._connect(spec.destination, creds, allow_private)

    @classmethod
    def with_client(cls, sftp: Any, base: str = "") -> "SftpDestination":
        """For tests: an SFTP client double."""
        self = cls.__new__(cls)
        self.base, self._sftp, self._transport = base, sftp, None
        return self

    def _connect(self, dest: dict[str, Any], creds: dict[str, str], allow_private: bool) -> None:
        import paramiko  # type: ignore[import-untyped]  # late: only SFTP jobs need it

        host, port = str(dest["host"]), int(dest["port"])
        address = resolve_checked(host, port, allow_private=allow_private)
        try:
            sock = socket.create_connection((address, port), timeout=CONNECT_TIMEOUT)
        except OSError as exc:
            raise DestinationError("DESTINATION_UNREACHABLE") from exc
        transport = paramiko.Transport(sock)
        try:
            transport.banner_timeout = CONNECT_TIMEOUT
            transport.start_client(timeout=CONNECT_TIMEOUT)
            seen = host_fingerprint(transport.get_remote_server_key().asbytes())
            pinned = str(dest.get("host_key_sha256") or "")
            if not pinned:
                raise DestinationError("SFTP_HOST_KEY_UNPINNED", seen)
            if not hmac.compare_digest(seen, pinned):
                raise DestinationError("SFTP_HOST_KEY_MISMATCH", seen)
            self._authenticate(transport, str(dest["username"]), creds)
            if not transport.is_authenticated():
                raise DestinationError("SFTP_AUTH_FAILED")
            self._sftp = paramiko.SFTPClient.from_transport(transport)
            self._transport = transport
        except DestinationError:
            transport.close()
            raise
        except (paramiko.SSHException, OSError, EOFError) as exc:
            transport.close()
            raise DestinationError("SFTP_FAILED") from exc

    @staticmethod
    def _authenticate(transport: Any, username: str, creds: dict[str, str]) -> None:
        import paramiko  # type: ignore[import-untyped]

        try:
            if creds.get("private_key"):
                passphrase = creds.get("passphrase") or None
                for loader in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
                    try:
                        pkey = loader.from_private_key(StringIO(creds["private_key"]), passphrase)
                    except (paramiko.SSHException, ValueError):
                        continue
                    transport.auth_publickey(username, pkey)
                    return
                raise DestinationError("SFTP_AUTH_FAILED")
            transport.auth_password(username, creds["password"])
        except paramiko.AuthenticationException:
            raise DestinationError("SFTP_AUTH_FAILED") from None

    # -- operations
    def _path(self, key: str) -> str:
        return _join(self.base, key) or "."

    def _mkdirs(self, directory: str) -> None:
        built = ""
        for part in [p for p in directory.split("/") if p]:
            built = f"{built}/{part}" if built else part
            try:
                self._sftp.stat(built)
            except OSError:
                self._sftp.mkdir(built)

    def test(self) -> None:
        probe = _join(self.base, f".filecanopy-probe-{secrets.token_hex(4)}")
        try:
            self._mkdirs(self.base)
            with self._sftp.open(probe, "wb") as handle:
                handle.write(b"ok")
            self._sftp.remove(probe)
        except OSError as exc:
            raise DestinationError("DESTINATION_NOT_WRITABLE") from exc

    def put(self, local: Path, key: str, sha256: str, size: int) -> None:
        final = self._path(key)
        tmp = f"{final}.part-{secrets.token_hex(4)}"
        try:
            self._mkdirs(os.path.dirname(final))
            self._sftp.put(str(local), tmp)
            if self._sftp.stat(tmp).st_size != size:
                raise DestinationError("UPLOAD_VERIFY_FAILED")
            try:
                self._sftp.posix_rename(tmp, final)
            except (OSError, AttributeError):
                self._sftp.rename(tmp, final)
        except DestinationError:
            self._quiet_remove(tmp)
            raise
        except OSError as exc:
            self._quiet_remove(tmp)
            raise DestinationError("UPLOAD_FAILED") from exc

    def get(self, key: str, local: Path) -> None:
        try:
            self._sftp.get(self._path(key), str(local))
        except OSError as exc:
            raise DestinationError("DOWNLOAD_FAILED") from exc

    def delete(self, key: str) -> None:
        try:
            self._sftp.remove(self._path(key))
        except FileNotFoundError:
            return
        except OSError as exc:
            raise DestinationError("DELETE_FAILED") from exc

    def _quiet_remove(self, path: str) -> None:
        try:
            self._sftp.remove(path)
        except OSError:
            pass

    def close(self) -> None:
        for item in (self._sftp, self._transport):
            try:
                if item is not None:
                    item.close()
            except Exception as exc:  # closing never fails a run
                log.debug("sftp close: %s", type(exc).__name__)


# ------------------------------------------------------------------ S3


class S3Destination:
    def __init__(self, spec: JobSpec, creds: dict[str, str], *, allow_private: bool) -> None:
        dest = spec.destination
        self.bucket = str(dest["bucket"])
        self.base = str(dest.get("path", ""))
        endpoint = str(dest.get("endpoint_url") or "")
        if endpoint:
            parts = urlsplit(endpoint)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
                or parts.path not in ("", "/")
            ):
                raise DestinationError("DESTINATION_INVALID")
            resolve_checked(parts.hostname, parts.port or 443, allow_private=allow_private)
        import boto3  # type: ignore[import-untyped]
        from botocore.config import Config  # type: ignore[import-untyped]

        self._client: Any = boto3.client(
            "s3",
            region_name=str(dest.get("region") or "") or None,
            endpoint_url=endpoint or None,
            aws_access_key_id=creds["access_key_id"],
            aws_secret_access_key=creds["secret_access_key"],
            config=Config(
                signature_version="s3v4",
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=60,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        if not allow_private:
            # Every request (retries and redirects included) re-checks where its host resolves
            # to right before it is sent, so a name that starts pointing inside after the check
            # above, or a redirect to an internal host, is refused. The remaining window is the
            # time between this check and the connection of the HTTP library.
            self._client.meta.events.register("before-send", _guard_request)

    @classmethod
    def with_client(cls, client: Any, bucket: str, base: str = "") -> "S3Destination":
        """For tests: an S3 client double."""
        self = cls.__new__(cls)
        self._client, self.bucket, self.base = client, bucket, base
        return self

    def _key(self, key: str) -> str:
        return _join(self.base, key)

    def test(self) -> None:
        probe = self._key(f".filecanopy-probe-{secrets.token_hex(4)}")
        try:
            self._client.head_bucket(Bucket=self.bucket)
            self._client.put_object(Bucket=self.bucket, Key=probe, Body=b"ok")
            self._client.delete_object(Bucket=self.bucket, Key=probe)
        except DestinationError:
            raise
        except Exception as exc:
            raise DestinationError(_s3_code(exc, "DESTINATION_NOT_WRITABLE")) from None

    def put(self, local: Path, key: str, sha256: str, size: int) -> None:
        remote = self._key(key)
        try:
            self._client.upload_file(
                str(local), self.bucket, remote, ExtraArgs={"Metadata": {"sha256": sha256}}
            )
            head = self._client.head_object(Bucket=self.bucket, Key=remote)
        except DestinationError:
            raise
        except Exception as exc:
            raise DestinationError(_s3_code(exc, "UPLOAD_FAILED")) from None
        if head.get("ContentLength") != size or head.get("Metadata", {}).get("sha256") != sha256:
            try:
                self.delete(key)
            except DestinationError:
                pass
            raise DestinationError("UPLOAD_VERIFY_FAILED")

    def get(self, key: str, local: Path) -> None:
        try:
            self._client.download_file(self.bucket, self._key(key), str(local))
        except DestinationError:
            raise
        except Exception as exc:
            raise DestinationError(_s3_code(exc, "DOWNLOAD_FAILED")) from None

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=self._key(key))
        except DestinationError:
            raise
        except Exception as exc:
            raise DestinationError(_s3_code(exc, "DELETE_FAILED")) from None

    def close(self) -> None:
        return None


def _guard_request(request: Any, **_: Any) -> None:
    parts = urlsplit(str(request.url))
    resolve_checked(parts.hostname or "", parts.port or 443, allow_private=False)


def _s3_code(exc: Exception, default: str) -> str:
    """A fixed code from a boto3 error. The message is dropped: it can name keys or accounts."""
    response = getattr(exc, "response", None)
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied", "AllAccessDisabled"):
        return "S3_AUTH_FAILED"
    if code in ("NoSuchBucket", "404"):
        return "S3_BUCKET_MISSING"
    return default


# ------------------------------------------------------------------ factory


def open_destination(spec: JobSpec, settings: Settings) -> Destination | None:
    """None for a local job. Credential or connection problems raise `DestinationError`."""
    if spec.destination_type == "local":
        return None
    try:
        creds = open_credentials(settings, spec)
    except JobError as exc:
        raise DestinationError(exc.code) from None
    allow = settings.backup_allow_private_destinations
    try:
        if spec.destination_type == "sftp":
            return SftpDestination(spec, creds, allow_private=allow)
        if spec.destination_type == "s3":
            return S3Destination(spec, creds, allow_private=allow)
    except DestinationError:
        raise
    except Exception:  # library errors can carry hosts or key names: only a fixed code leaves
        raise DestinationError("DESTINATION_FAILED") from None
    raise DestinationError("DESTINATION_TYPE_INVALID")
