# FileCanopy documentation

## Using FileCanopy

| Guide | What it covers |
|---|---|
| [Install with Docker](guides/docker-install.md) | The three-step install and restarts. |
| [First-run setup](guides/first-run-setup.md) | Setup token, administrator account, optional import. |
| [Configuration](guides/configuration.md) | Ports, folders, upload limits and other `.env` settings. |
| [Remote access](guides/remote-access.md) | Using it from other devices behind a TLS reverse proxy. |
| [Your data, updating and stopping](guides/data-and-updates.md) | Where files live, how to update, how to stop. |
| [Backups](guides/backups.md) | Disaster-recovery backups and encrypted in-app backups. |
| [Security and privacy](guides/security-and-privacy.md) | What FileCanopy protects and what it does not. |
| [Production guide](guides/production.md), [hardening](guides/hardening.md) | Pinned versions, restore, HTTPS, Docker Swarm, logging. |

## AI features

FileCanopy is built around AI. Start with [Set up AI](guides/ai.md), then [AI organize](guides/ai-organize.md), [PDF split](guides/ai-split.md), [semantic search](guides/semantic-search.md), [document chat](guides/document-chat.md), [collections](guides/collections.md), and [MCP](guides/mcp.md).

Also optional: [inbound mailbox](guides/inbound-mailbox.md), [Google sign-in](guides/google-sign-in.md).

## Release material

Licensing, dependency and container records are in [`release/`](release/).

## Developing

[Developing and contributing](development.md) and the [architecture decision records](adr/README.md).
