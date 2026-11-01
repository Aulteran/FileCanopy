# Running FileCanopy behind a reverse proxy

FileCanopy's web container listens on `127.0.0.1:8080` only. To use it from your phone or from
outside your home, put a reverse proxy with HTTPS in front of it. This page has a working setup
for each common proxy. Design and reasons: [ADR-018](../../docs/adr/ADR-018-reverse-proxy-and-https.md).

**What was tested.** The Compose test `tests/integration/compose/test_proxy_stack.py` starts the
real stack behind three real proxy containers with HTTPS: nginx (configured like Nginx Proxy
Manager, using `npm-advanced.conf` from this folder), Caddy and Traefik. It checks the real
client address, that a spoofed `X-Forwarded-For` is ignored, sign-in and cookies over HTTPS, the
origin check with and without `Host`, a 40 MiB upload and download, and HSTS. **Not run here:**
Nginx Proxy Manager itself (its directives are tested in plain nginx) and Cloudflare Tunnel (it
needs a Cloudflare account).

## The three settings that matter

Put them in `deploy/.env`, then run `docker compose -f deploy/docker-compose.yml up -d`.

| Setting | What to put there |
|---|---|
| `FILECANOPY_PUBLIC_URL` | The address you type in the browser, for example `https://files.example.org`. No path. With it, sign-in works even if the proxy does not pass on the original `Host` header. |
| `FILECANOPY_TRUSTED_PROXIES` | The IP address (or range) the proxy has **as the FileCanopy web container sees it**. Only these addresses are believed when they say who the visitor is. |
| `FILECANOPY_COOKIE_SECURE` | Leave it `true` (the default). |

### Why the trusted proxy setting matters

A proxy tells FileCanopy who the visitor is in the `X-Forwarded-For` header. Anyone can send that
header too, so FileCanopy only believes it from the addresses in `FILECANOPY_TRUSTED_PROXIES`.

- **Empty (the default): no proxy is trusted.** Everything still works, but every visitor looks
  like the proxy. The sign-in limit (40 failures per 15 minutes per address) is then shared by
  everyone, so one person's mistakes can lock all users out, and the audit log shows the proxy
  address.
- **Set to your proxy:** each visitor is counted separately and the audit log shows who really
  connected. A header sent by anyone else is ignored.

How to find the value: run `docker network inspect <network>` and look at the proxy
container's `IPv4Address`, or use the whole subnet of a network that only contains your proxy
(for example `172.18.0.0/16`). If the proxy runs on the host rather than in Docker, FileCanopy
sees it at the gateway address of the `filecanopy_default` network (`docker network inspect
filecanopy_default`, field `Gateway`, often `172.x.0.1`).

**Do not** trust a wider range than your proxies. Anything in the range can pretend to be any
visitor. `0.0.0.0/0` and `::/0` are refused and the container will not start. After changing the
setting, check it: sign in with a wrong password, then run
`docker compose -f deploy/docker-compose.yml exec postgres psql -U filecanopy -d filecanopy -c "select at, action, ip from auth_audit order by id desc limit 3"`.
The `ip` of the newest `login.failed` row should be your own address, not the proxy's.

If a CDN sits in front of your proxy (Cloudflare, then Caddy), add the CDN's addresses as well,
or have the proxy put the real address in `X-Forwarded-For` for you (Caddy's `trusted_proxies`,
Traefik's `forwardedHeaders.trustedIPs`, nginx's `real_ip` module). FileCanopy reads the chain from
the right and takes the first address that is not a trusted proxy.

## Joining the proxy's Docker network

If your proxy runs in Docker, the published `127.0.0.1:8080` port is not reachable from another
container. Join the proxy's network instead:

```
# deploy/.env
FILECANOPY_PROXY_NETWORK=proxy          # name from `docker network ls`
FILECANOPY_TRUSTED_PROXIES=172.18.0.0/16  # that network's subnet

docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.proxy.yml up -d
```

The proxy then forwards to `http://web:8080`. The container name `filecanopy-web-1` also works. Only
the web container joins; the database and API stay on the private network.

## Upload size, timeouts and the Cloudflare limit

Files are uploaded as one streamed request, up to `FILECANOPY_MAX_UPLOAD_BYTES` (10 GiB by
default). A proxy has to let that through:

- **nginx and Nginx Proxy Manager:** the default body limit is 1 MB and nginx writes the whole
  upload to disk before passing it on. `npm-advanced.conf` turns both off and raises the
  timeouts. (The test proves a stock nginx answers `413` to a 2 MiB upload.)
- **Caddy and Traefik:** no request size limit by default. Do not set one lower than your
  largest file. Keep `flush_interval -1` in Caddy so downloads stream.
- **Cloudflare (proxied DNS or Cloudflare Tunnel):** Cloudflare refuses request bodies over
  **100 MB on the Free and Pro plans** (more on paid plans; check Cloudflare's current limits). A bigger single upload fails with a Cloudflare error that
  FileCanopy never sees. Options: upload large files from inside your network, give that
  hostname a DNS-only (grey cloud) record, or use a different path (VPN or Tailscale). Resumable
  chunked upload is a separate decision and is not built.

## Nginx Proxy Manager

1. Put NPM and FileCanopy on one Docker network (see "Joining the proxy's Docker network"). NPM cannot use the `127.0.0.1:8080` port from inside its container.
2. **Proxy Hosts, Add Proxy Host.** Domain name `files.example.org`. Scheme `http`. Forward
   hostname `web` (or `filecanopy-web-1`), port `8080`. Leave "Block Common Exploits" on.
   Websockets are not needed.
3. **SSL tab.** Request a Let's Encrypt certificate, tick "Force SSL" and "HTTP/2". Enable HSTS in
   NPM or with `FILECANOPY_HSTS=true`, not both (see below).
4. **Advanced tab.** Paste the contents of [`npm-advanced.conf`](npm-advanced.conf).
5. In `deploy/.env` set `FILECANOPY_PUBLIC_URL=https://files.example.org` and
   `FILECANOPY_TRUSTED_PROXIES` to NPM's address on the shared network. Then start FileCanopy
   with the proxy override.

NPM sends `Host`, `X-Forwarded-For` and `X-Forwarded-Proto` by default. If NPM is reached on a
port other than 443, `FILECANOPY_PUBLIC_URL` must include it (`https://files.example.org:8443`).

## Plain nginx, Caddy, Traefik

- nginx: [`nginx.conf`](nginx.conf) plus a copy of `npm-advanced.conf`.
- Caddy: [`Caddyfile`](Caddyfile). Caddy fetches the certificate by itself.
- Traefik: [`traefik-dynamic.yml`](traefik-dynamic.yml) for the file provider. Your static config
  needs the `websecure` entry point and a certificate resolver named `letsencrypt` (or edit the
  file).

## Cloudflare Tunnel

[`cloudflared.yml`](cloudflared.yml) points the tunnel at `http://web:8080`. Cloudflare adds
`CF-Connecting-IP` and `X-Forwarded-For`. Trust the address `cloudflared` has on the Docker
network. Read the body limit above before relying on it for large files.

## HSTS

`FILECANOPY_HSTS=true` makes the web container send `Strict-Transport-Security: max-age=31536000`
on requests that a trusted proxy says came in over HTTPS. Browsers will then refuse plain HTTP
for that name for a year, which is hard to undo, so turn it on only after HTTPS works for good.
It is off by default. If your proxy already sends HSTS, leave this off.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Sign-in answers `ORIGIN_NOT_ALLOWED` (403) | The proxy changes the `Host` header. Set `FILECANOPY_PUBLIC_URL` to the address in the browser. |
| Sign-in works but you are signed out at once | The browser reached the site over HTTP, or `FILECANOPY_COOKIE_SECURE` was set to `false`. Use HTTPS. |
| Everyone is locked out together after a few typos | `FILECANOPY_TRUSTED_PROXIES` is empty or wrong, so all visitors share the proxy's address. |
| Upload stops at 1 MB with `413` | nginx body limit, see `npm-advanced.conf`. |
| Big upload fails after about 100 MB with a Cloudflare page | Cloudflare body limit, see above. |
| Upload or download ends after 60 seconds | Proxy timeouts. Raise them (`npm-advanced.conf`). |
| Container `web` will not start and the log names `FILECANOPY_TRUSTED_PROXIES` | The value is not a list of IPs or CIDR ranges, or it is `0.0.0.0/0`. |
