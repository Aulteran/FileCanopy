"""OIDC relying party (M12, ADR-020): discovery, PKCE, code exchange and ID token validation.

Only standards, no provider specifics: it works with Authentik, Authelia, Keycloak and the like.
The configured issuer is operator input (never request input), endpoints must keep the issuer's
scheme, redirects are not followed, replies are size-capped and every failure maps to a short
code. Nothing here logs tokens, codes, secrets or claims.
"""

import base64
import hashlib
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx
import jwt

from app.config import Settings

ALLOWED_ALGS = ("RS256", "PS256", "ES256")  # asymmetric only: never "none", never HS*
MAX_BODY = 1024 * 1024
DISCOVERY_TTL = 3600.0
FAILURE_BACKOFF = 30.0  # after a failed discovery or key fetch, fail fast for this long
JWKS_TTL = 3600.0
JWKS_MIN_REFRESH = 60.0
LEEWAY_SECONDS = 60


class OidcError(Exception):
    """`code` is the only thing shown to people; the cause is never included."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def new_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


@dataclass
class Tokens:
    id_token: str
    access_token: str | None


@dataclass
class _Discovery:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: str | None
    token_auth_post: bool
    fetched: float


ClientFactory = Callable[[], httpx.Client]


class OidcProvider:
    def __init__(self, settings: Settings, client_factory: ClientFactory | None = None) -> None:
        self._s = settings
        self._factory = client_factory or (
            lambda: httpx.Client(timeout=settings.oidc_timeout_seconds, follow_redirects=False)
        )
        self._lock = threading.Lock()
        self._discovery: _Discovery | None = None
        self._jwks: tuple[float, dict[str, Any]] | None = None
        self._jwks_last_try = 0.0
        self._down_until = 0.0

    # --------------------------------------------------------------- http

    def _check_url(self, url: str) -> None:
        parsed = urlparse(url)
        loopback = (parsed.hostname or "") in ("localhost", "127.0.0.1", "::1")
        ok = parsed.scheme == "https" or (
            parsed.scheme == "http" and (loopback or self._s.oidc_allow_http)
        )
        if not ok or not parsed.netloc or parsed.fragment:
            raise OidcError("OIDC_UNAVAILABLE")

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        self._check_url(url)
        try:
            with (
                self._factory() as client,
                client.stream(
                    method, url, data=data, headers=headers, auth=auth, follow_redirects=False
                ) as resp,
            ):
                body = b""
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BODY:
                        raise OidcError("OIDC_UNAVAILABLE")
                status = resp.status_code
        except (httpx.HTTPError, OSError) as exc:
            raise OidcError("OIDC_UNAVAILABLE") from exc
        if status != 200:
            raise OidcError("OIDC_UNAVAILABLE" if status >= 500 or status == 404 else "OIDC_FAILED")
        try:
            doc = httpx.Response(200, content=body).json()
        except ValueError as exc:
            raise OidcError("OIDC_UNAVAILABLE") from exc
        if not isinstance(doc, dict):
            raise OidcError("OIDC_UNAVAILABLE")
        return doc

    # ---------------------------------------------------------- discovery

    def _discover(self) -> _Discovery:
        with self._lock:
            d = self._discovery
            if d is not None and time.monotonic() - d.fetched < DISCOVERY_TTL:
                return d
            if time.monotonic() < self._down_until:
                raise OidcError("OIDC_UNAVAILABLE")  # a dead provider must not tie up workers
        try:
            return self._fetch_discovery()
        except OidcError:
            with self._lock:
                self._down_until = time.monotonic() + FAILURE_BACKOFF
            raise

    def _fetch_discovery(self) -> _Discovery:
        issuer = self._s.oidc_issuer
        doc = self._request("GET", issuer.rstrip("/") + "/.well-known/openid-configuration")
        if doc.get("issuer") != issuer:
            raise OidcError("OIDC_UNAVAILABLE")  # mix-up / misconfiguration: refuse
        methods = doc.get("code_challenge_methods_supported")
        if isinstance(methods, list) and "S256" not in methods:
            raise OidcError("OIDC_UNAVAILABLE")
        urls: dict[str, str] = {}
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            value = doc.get(key)
            if not isinstance(value, str):
                raise OidcError("OIDC_UNAVAILABLE")
            self._check_url(value)
            urls[key] = value
        userinfo = doc.get("userinfo_endpoint")
        if isinstance(userinfo, str):
            self._check_url(userinfo)
        auths = doc.get("token_endpoint_auth_methods_supported")
        post_only = (
            isinstance(auths, list)
            and "client_secret_basic" not in auths
            and "client_secret_post" in auths
        )
        d = _Discovery(
            urls["authorization_endpoint"],
            urls["token_endpoint"],
            urls["jwks_uri"],
            userinfo if isinstance(userinfo, str) else None,
            post_only,
            time.monotonic(),
        )
        with self._lock:
            self._discovery = d
        return d

    def _signing_keys(self, force: bool) -> dict[str, Any]:
        with self._lock:
            cached = self._jwks
            fresh = cached is not None and time.monotonic() - cached[0] < JWKS_TTL
            if cached is not None and fresh and not force:
                return cached[1]
            if force and time.monotonic() - self._jwks_last_try < JWKS_MIN_REFRESH:
                return cached[1] if cached else {}
            self._jwks_last_try = time.monotonic()
        try:
            doc = self._request("GET", self._discover().jwks_uri)
        except OidcError:
            with self._lock:
                self._down_until = time.monotonic() + FAILURE_BACKOFF
            raise
        keys = doc.get("keys")
        if not isinstance(keys, list):
            raise OidcError("OIDC_UNAVAILABLE")
        out: dict[str, Any] = {}
        for jwk in keys:
            if isinstance(jwk, dict) and isinstance(jwk.get("kid"), str):
                if jwk.get("use") not in (None, "sig"):
                    continue
                out[jwk["kid"]] = jwk
        with self._lock:
            self._jwks = (time.monotonic(), out)
        return out

    # ------------------------------------------------------------- flow

    def authorization_url(
        self, state: str, nonce: str, challenge: str, *, reauth: bool = False
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": self._s.oidc_client_id,
            "redirect_uri": self._s.oidc_redirect_uri,
            "scope": self._s.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if self._s.oidc_hosted_domain:
            params["hd"] = self._s.oidc_hosted_domain  # a hint only; the token is checked too
        if reauth:
            # Ask the provider to authenticate the person again. Google has no "login" prompt and
            # shows its account chooser instead; the callback checks `auth_time` when present.
            params["max_age"] = "0"
            params["prompt"] = "select_account" if self._s.oidc_is_google else "login"
        base = self._discover().authorization_endpoint
        return f"{base}{'&' if '?' in base else '?'}{urlencode(params)}"

    def exchange_code(self, code: str, verifier: str) -> Tokens:
        d = self._discover()
        secret = self._s.oidc_client_secret.get_secret_value()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._s.oidc_redirect_uri,
            "code_verifier": verifier,
            "client_id": self._s.oidc_client_id,
        }
        auth: tuple[str, str] | None = None
        if secret and d.token_auth_post:
            form["client_secret"] = secret
        elif secret:
            auth = (quote(self._s.oidc_client_id, safe=""), quote(secret, safe=""))
        doc = self._request(
            "POST", d.token_endpoint, data=form, headers={"Accept": "application/json"}, auth=auth
        )
        id_token = doc.get("id_token")
        access = doc.get("access_token")
        if not isinstance(id_token, str) or not id_token:
            raise OidcError("OIDC_FAILED")
        return Tokens(id_token, access if isinstance(access, str) else None)

    def verify_id_token(self, id_token: str, nonce: str) -> dict[str, Any]:
        """Signature, issuer, audience, expiry and nonce. Returns the claims."""
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise OidcError("ID_TOKEN_INVALID") from exc
        alg = header.get("alg")
        kid = header.get("kid")
        if alg not in ALLOWED_ALGS:
            raise OidcError("ID_TOKEN_INVALID")
        keys = self._signing_keys(force=False)
        if not isinstance(kid, str) or kid not in keys:
            keys = self._signing_keys(force=True)  # key rotation: refresh at most once a minute
        if not isinstance(kid, str) or kid not in keys:
            raise OidcError("ID_TOKEN_INVALID")
        try:
            key = jwt.PyJWK(keys[kid]).key
            claims = jwt.decode(
                id_token,
                key,
                algorithms=[alg],
                audience=self._s.oidc_client_id,
                issuer=self._accepted_issuers(),
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise OidcError("ID_TOKEN_INVALID") from exc
        aud = claims.get("aud")
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != self._s.oidc_client_id:
            raise OidcError("ID_TOKEN_INVALID")
        if claims.get("azp") not in (None, self._s.oidc_client_id):
            raise OidcError("ID_TOKEN_INVALID")
        if self._s.oidc_hosted_domain:
            hd = claims.get("hd")
            if not isinstance(hd, str) or hd.lower() != self._s.oidc_hosted_domain.lower():
                raise OidcError("OIDC_DOMAIN_NOT_ALLOWED")
        got = claims.get("nonce")
        if not isinstance(got, str) or not secrets.compare_digest(got, nonce):
            raise OidcError("ID_TOKEN_INVALID")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub or len(sub) > 255:
            raise OidcError("ID_TOKEN_INVALID")
        return dict(claims)

    def _accepted_issuers(self) -> list[str]:
        """The configured issuer; Google also signs tokens with the bare host name."""
        issuer = self._s.oidc_issuer
        if self._s.oidc_is_google:
            return ["https://accounts.google.com", "accounts.google.com"]
        return [issuer]

    def auth_age_ok(self, claims: dict[str, Any], max_age_seconds: int = 300) -> bool:
        """For a re-authentication: when the provider says when the person last signed in
        (`auth_time`), it must be recent. Providers that omit it are accepted."""
        at = claims.get("auth_time")
        if at is None:
            return True
        if isinstance(at, bool) or not isinstance(at, int | float):
            return False
        return time.time() - float(at) <= max_age_seconds + LEEWAY_SECONDS

    def verified_email(self, claims: dict[str, Any], access_token: str | None) -> str | None:
        """The email the provider vouches for, or None. Falls back to the userinfo endpoint (some
        providers keep claims out of the ID token); its `sub` must equal the ID token's."""
        email, verified = claims.get("email"), claims.get("email_verified")
        if not isinstance(email, str):
            d = self._discover()
            if d.userinfo_endpoint is None or access_token is None:
                return None
            info = self._request(
                "GET", d.userinfo_endpoint, headers={"Authorization": f"Bearer {access_token}"}
            )
            if info.get("sub") != claims.get("sub"):
                raise OidcError("ID_TOKEN_INVALID")
            email, verified = info.get("email"), info.get("email_verified")
        if not isinstance(email, str) or not email:
            return None
        if self._s.oidc_require_email_verified and verified is not True:
            return None
        return email.strip().lower()
