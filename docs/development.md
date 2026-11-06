# Developing and contributing

## Build from source

```bash
git clone https://github.com/Aulteran/FileCanopy.git
cd FileCanopy
docker compose -f deploy/compose.yaml -f deploy/contrib/build.yaml up -d --build --wait
```

Use `-f deploy/compose.yaml` (and the same build file) with the other `docker compose` commands in the guides. The app is then at http://127.0.0.1:8080.

## For developers

Requirements: Node.js 22 or newer, and Docker for the backend and end-to-end checks.

```bash
npm install
npm run check        # lint, format, unit tests and secret scan
npm run check:api    # Python checks in a pinned container
npm run test:compose # real Compose stack tests
npm run check:web    # web type checks and unit tests
npm run test:e2e     # browser tests against a real stack
npm run check:full   # everything above
```

Continuous integration runs on GitHub Actions. See `.github/workflows/ci.yml`.

## Contributing

Report bugs in [GitHub issues](https://github.com/Aulteran/FileCanopy/issues), using synthetic examples and sanitised diagnostics. Include your commit/image identity, deployment method and reproduction steps. Discuss large changes first. [CONTRIBUTING](release/drafts/CONTRIBUTING.md), [SECURITY](../SECURITY.md) and [CHANGELOG](release/drafts/CHANGELOG.md) are prepared as drafts pending owner approval. A private security-reporting route still needs configuration and verification; do not post secrets or vulnerability details in public issues.

## License

FileCanopy declares [GNU Affero General Public License v3.0 only](../LICENSE) (`AGPL-3.0-only`), consistently with both package manifests. Third-party components retain their own terms. The [runtime dependency audit](release/dependency-audit.md) records exact versions, sources and unresolved licence/redistribution issues; release clearance is pending their resolution and owner sign-off. No legal advice or blanket compatibility conclusion is implied.

## Planning and status files

These files are the project's written memory, for people and for AI coding agents. `CLAUDE.md` at the repository root points agents to them.

- [`PROJECT_STATUS.md`](PROJECT_STATUS.md) and [`MILESTONES.json`](MILESTONES.json): current state, per milestone.
- [`01_PRODUCT_SPEC.md`](01_PRODUCT_SPEC.md) to [`11_OPEN_DECISIONS.md`](11_OPEN_DECISIONS.md): product, architecture, security, data, AI, mail, UI, plan, testing and open decisions.
- [`adr/`](adr/README.md): architecture decision records. [`milestones/`](milestones/): design and plan per milestone.
- [`release/`](release/README.md): release evidence, known issues and audits.
