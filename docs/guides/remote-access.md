# Remote access

The web app listens only on `127.0.0.1`, so it is reachable only from the same machine. To use it from other devices, put a TLS reverse proxy in front of it and keep `FILECANOPY_COOKIE_SECURE=true`. Do not expose the plain HTTP port to a network.

Working, tested setups for **Nginx Proxy Manager**, nginx, Caddy, Traefik and Cloudflare Tunnel are in [`deploy/proxy/`](../../deploy/proxy/README.md). In short, set these in `.env`:

| Setting | Meaning |
|---|---|
| `FILECANOPY_PUBLIC_URL` | The address you type in the browser, for example `https://files.example.org`. |
| `FILECANOPY_TRUSTED_PROXIES` | The address of your reverse proxy as the app sees it (its own single address, not a whole network). Empty (default) trusts nobody, and then all users share the proxy's address for sign-in limits and the audit log. |
| `FILECANOPY_HSTS` | `true` sends HSTS. Off by default. |

If your proxy runs in Docker, add [`deploy/optional/compose.proxy.yaml`](../../deploy/optional/compose.proxy.yaml) as a second `-f` file and set `FILECANOPY_PROXY_NETWORK` so the proxy can reach the app at `http://web:8080`. Uploads can be many GiB: the proxy must allow large bodies and long timeouts (the example files do). Cloudflare limits request bodies to 100 MB on the Free and Pro plans, which also applies to Cloudflare Tunnel; see the proxy guide.
