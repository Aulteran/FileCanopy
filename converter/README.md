# converter

Optional sealed container that turns Word, Excel and PowerPoint (and OpenDocument) files into PDF so
the viewer can show them (M10, ADR-019 section 5). Off unless the `converter` Compose profile is
started and `FILECANOPY_CONVERTER_URL=unix:///run/converter/converter.sock` is set for the API.

What seals it (see the `converter` service in `deploy/docker-compose.yml`): it has **no network at all**
(`network_mode: none`); the API talks to it through a Unix socket in a volume shared by those two
containers only, so it cannot reach the internet, the API's HTTP port, the database or DNS; it never
sees the library, app data or secrets; read-only root with a small tmpfs;
non-root user, all capabilities dropped, `no-new-privileges`, memory, CPU and process limits. Every
conversion uses a fresh LibreOffice profile with macro security at its highest level, active content
and link updates off, a 60 s wall-clock kill of the whole process group, CPU and file-size limits,
and a page cap. Output is checked again by the API before it is cached.
LibreOffice (MPL-2.0) is installed unmodified from the Debian archive and runs as a separate program.
