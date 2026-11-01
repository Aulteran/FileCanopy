# ADR-018: Reverse proxy, HTTPS and trusted proxy configuration

- **Status:** Accepted (owner, 2026-11-01)
- **Date:** 2026-11-01
- **Milestone:** M07c

## Context

FileCanopy publishes only `127.0.0.1:8080` (the bundled nginx `web` service). To use it from other devices the owner puts their own TLS reverse proxy (Nginx Proxy Manager first, then Caddy, nginx, Traefik, Cloudflare Tunnel) in front. A code and config review on 2026-11-01 found this works for basic browsing but has gaps, and none of it is tested:

1. **Client IP is wrong behind an outer proxy.** The bundled nginx sets `X-Forwarded-For $remote_addr` (it overwrites the header) and Compose sets `FILECANOPY_TRUSTED_PROXY_HOPS=1`. Behind an outer proxy `$remote_addr` is the outer proxy, so every user shares one IP. The per-IP login throttle (40 per 15 minutes) then locks all users out together, and audit rows record the proxy address.
2. **`X-Forwarded-Proto` is always `http`** (nginx `$scheme`). The API does not read it today, but any future use (absolute links in email, MCP, OIDC redirects) would be wrong.
3. **Origin check depends on `Host`.** `check_origin` accepts an `Origin` only if its host equals the request `Host` or it is listed in `FILECANOPY_ALLOWED_ORIGINS`. nginx forwards `Host`, so this works only if the outer proxy preserves the public host name (Caddy, Traefik and Cloudflare Tunnel do; nginx needs `proxy_set_header Host $host`). `FILECANOPY_ALLOWED_ORIGINS` exists in the API but Compose does not pass it through.
4. **Upload path.** Uploads are one raw streamed request (up to 10 GiB). An outer nginx defaults to `client_max_body_size 1m` and request buffering; Cloudflare caps request bodies (100 MB on Free and Pro), which also applies to Cloudflare Tunnel. Neither is documented.
5. **No HSTS**, no documented proxy network setup (the `127.0.0.1` binding is unreachable from a proxy in another container), and the README has only one sentence, no examples.

## Decision

- Replace the hop count with an explicit trusted-proxy list: `FILECANOPY_TRUSTED_PROXIES` (CIDRs) in nginx using `ngx_http_realip_module` (`set_real_ip_from`, `real_ip_header X-Forwarded-For`, `real_ip_recursive on`), so nginx derives the real client address and then forwards a single clean `X-Forwarded-For` to the API. Default empty: the direct peer is used and a spoofed header is ignored. `FILECANOPY_TRUSTED_PROXY_HOPS` is no longer an owner setting: Compose fixes it at 1 because nginx now hands the API one clean address.
- Forward `X-Forwarded-Proto` from the outer proxy only when that proxy is trusted; otherwise use nginx `$scheme`.
- Pass `FILECANOPY_ALLOWED_ORIGINS` through Compose and add a `FILECANOPY_PUBLIC_URL` documented as the recommended setting; the Origin check accepts it.
- Optional `FILECANOPY_HSTS=true` header from nginx, off by default (a mistaken HSTS is hard to undo on a home domain).
- Ship tested examples in `deploy/proxy/`: Nginx Proxy Manager (owner's own setup, a primary target), Caddy, nginx, Traefik, Cloudflare Tunnel, each with the upload size and timeout settings. Add a Compose override that joins a user-named external proxy network.
- Document the Cloudflare body-size limit honestly. Chunked or resumable single-file upload is a separate decision if owners need it; this ADR does not add it.

## Consequences

Per-IP throttling and audit work correctly behind a proxy. Misconfiguration (trusting too much) is the main risk, so the default stays "trust nothing" and the docs say how to check.

## Alternatives considered

- Keep hop counts: breaks when the number of proxies differs per owner (Cloudflare plus Caddy is two).
- Remove the bundled nginx and let the API face the proxy: loses streaming and CSP behaviour the stack relies on.
- Terminate TLS in the bundled stack (built-in Caddy): bigger product change, port 80/443 exposure by default, not chosen without owner approval.

## Security and privacy impact

Trusting a forwarded header from an untrusted peer lets a user spoof their IP and dodge throttling; the trusted-proxy list prevents that. Tests must prove a spoofed `X-Forwarded-For` from an untrusted peer is ignored. Cookies stay `__Host-`, Secure, HttpOnly, SameSite=Strict.
