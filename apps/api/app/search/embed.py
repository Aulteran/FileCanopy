"""Client for the local embedding endpoint (M19, docs/05_AI_AND_SEARCH.md "Optional embedding").

Only local kinds exist here: llama.cpp's OpenAI-compatible server (`/v1/embeddings`) and Ollama
(`/api/embed`). There is no cloud kind and no fallback, so text can reach nothing but the address an
administrator saved, and that address must resolve to loopback or private addresses only (the M15
check, repeated at every request; the request connects to the address that was checked). Text is
handed over as a `ClearedInput` built for the "local" mode, so the privacy gate that guards every
AI path also guards embeddings. Error bodies are never copied: they can echo the input.
"""

import json
import math
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.ai.providers import (
    EndpointError,
    Resolver,
    check_local_host,
    system_resolver,
    validate_endpoint,
    validate_model,
)
from app.ai.routing import AiRefused, ClearedInput, assert_deliverable

KINDS = ("openai_compatible", "ollama")
MIN_DIM = 8
MAX_DIM = 2000  # the largest size pgvector can index; bigger models are out of scope
MAX_PREFIX = 120
REFERENCE_MIN_COSINE = 0.98
# Fixed synthetic texts for verification. They contain no document content.
PROBES = (
    "FileCanopy embedding health check: a short sentence about invoices and insurance.",
    "A second, different probe text about holiday photographs from the mountains.",
)


class EmbedError(Exception):
    """A failed embedding call or an unusable answer. `code` is short and safe to show."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EndpointSpec:
    kind: str
    endpoint: str
    model: str

    @property
    def signature(self) -> str:
        return f"{self.kind}|{self.endpoint}|{self.model}"


def validate_spec(
    kind: str, endpoint: str, model: str, resolver: Resolver, allow: frozenset[str]
) -> EndpointSpec:
    """Raises ValueError (shape) or EndpointError (not a local address)."""
    if kind not in KINDS:
        raise ValueError("embedding endpoints are local only")
    return EndpointSpec(
        kind, validate_endpoint(kind, endpoint, resolver, allow), validate_model(model)
    )


def normalise(vec: list[float]) -> list[float]:
    """Unit length, so cosine similarity is a dot product. Rejects NaN, infinity and zero."""
    if not vec or any(not math.isfinite(x) for x in vec):
        raise EmbedError("BAD_VECTOR")
    peak = max(abs(x) for x in vec)  # scale first: the sum of squares could overflow to infinity
    if peak < 1e-12:
        raise EmbedError("BAD_VECTOR")
    scaled = [x / peak for x in vec]
    norm = math.sqrt(sum(x * x for x in scaled))
    out = [x / norm for x in scaled]
    if not all(math.isfinite(x) for x in out):
        raise EmbedError("BAD_VECTOR")
    return out


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class EmbeddingClient:
    def __init__(
        self,
        spec: EndpointSpec,
        *,
        timeout: float,
        max_response_bytes: int,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver = system_resolver,
        allow_hosts: frozenset[str] = frozenset(),
    ) -> None:
        self.spec = spec
        self._timeout = timeout
        self._limit = max_response_bytes
        self._resolver = resolver
        self._allow = allow_hosts
        self._client = httpx.Client(
            timeout=timeout, follow_redirects=False, transport=transport, trust_env=False
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, texts: list[str]) -> tuple[str, dict[str, Any]]:
        base = self.spec.endpoint
        if self.spec.kind == "ollama":
            return f"{base}/api/embed", {"model": self.spec.model, "input": texts}
        return f"{base}/v1/embeddings", {"model": self.spec.model, "input": texts}

    def _parse(self, data: Any, expected: int) -> list[list[float]]:
        try:
            if self.spec.kind == "ollama":
                out = [[float(x) for x in row] for row in data["embeddings"]]
            else:
                rows = sorted(data["data"], key=lambda r: int(r["index"]))
                out = [[float(x) for x in r["embedding"]] for r in rows]
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise EmbedError("BAD_RESPONSE") from exc
        if len(out) != expected:
            raise EmbedError("BAD_RESPONSE")
        return out

    def embed(self, cleared: list[ClearedInput]) -> list[list[float]]:
        """One normalised vector per input, in order. Inputs must be cleared for "local"."""
        texts: list[str] = []
        for item in cleared:
            try:
                texts.append(assert_deliverable(item, "local").text)
            except AiRefused:
                raise EmbedError("NOT_CLEARED") from None
        if not texts:
            return []
        url, body = self._request(texts)
        parts = urlparse(url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            ips = check_local_host(host, port, self._resolver, self._allow)
        except EndpointError:
            raise EmbedError("OUTAGE") from None
        ip = f"[{ips[0]}]" if ":" in ips[0] else ips[0]
        pinned = parts._replace(netloc=f"{ip}:{port}").geturl()
        extensions: dict[str, Any] = {}
        if parts.scheme == "https":
            extensions["sni_hostname"] = host
        deadline = time.monotonic() + self._timeout
        try:
            with self._client.stream(
                "POST", pinned, headers={"host": parts.netloc}, json=body, extensions=extensions
            ) as r:
                if r.status_code in (401, 403):
                    raise EmbedError("AUTH")
                if r.status_code >= 500 or 300 <= r.status_code < 400:
                    raise EmbedError("OUTAGE")
                if r.status_code >= 400:
                    raise EmbedError("BAD_RESPONSE")
                chunks: list[bytes] = []
                size = 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > self._limit:
                        raise EmbedError("TOO_LARGE")
                    if time.monotonic() > deadline:  # whole-request deadline, not per read
                        raise EmbedError("TIMEOUT")
                    chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise EmbedError("TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise EmbedError("OUTAGE") from exc
        try:
            data = json.loads(b"".join(chunks))
        except ValueError as exc:
            raise EmbedError("BAD_RESPONSE") from exc
        vectors = [normalise(v) for v in self._parse(data, len(texts))]
        if len({len(v) for v in vectors}) != 1:
            raise EmbedError("DIMENSION_MISMATCH")
        return vectors


def verify(client: EmbeddingClient, make_input: Any) -> tuple[int, list[list[float]]]:
    """Health and dimension check: two probe texts, twice. Returns the (stable) dimension and the
    probe vectors, which are kept as the model fingerprint (see `matches_reference`).

    The answers must be finite, non-zero, equal in length across both calls, within the supported
    range, and different probes must not collapse to the same vector."""
    first = client.embed([make_input(t) for t in PROBES])
    second = client.embed([make_input(t) for t in PROBES])
    dim = len(first[0])
    if not (MIN_DIM <= dim <= MAX_DIM):
        raise EmbedError("DIMENSION_UNSUPPORTED")
    if any(len(v) != dim for v in first + second):
        raise EmbedError("DIMENSION_MISMATCH")
    if cosine(first[0], first[1]) > 0.9999:
        raise EmbedError("DEGENERATE")
    if cosine(first[0], second[0]) < 0.98:
        raise EmbedError("UNSTABLE")
    return dim, first


def matches_reference(vectors: list[list[float]], reference: list[list[float]]) -> bool:
    """True when the probes embed (almost) as they did at verification: the same model answers.
    A model swapped behind the same address and name changes these vectors even when their size
    stays the same."""
    if len(vectors) != len(reference) or not reference:
        return False
    return all(
        len(v) == len(r) and cosine(v, r) >= REFERENCE_MIN_COSINE
        for v, r in zip(vectors, reference, strict=True)
    )
