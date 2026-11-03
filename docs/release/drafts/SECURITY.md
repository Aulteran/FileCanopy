# Security policy — draft for owner approval

Not active until the owner approves it, configures a private reporting destination and verifies
that a reporter can use it. No security response SLA, supported-version window, bounty, safe-harbour
promise or external-audit certification is implied.

## Reporting a suspected vulnerability

Proposed route: GitHub private vulnerability reporting on this repository, **if enabled and
verified by the owner**. Its current availability has not been established. Before adoption,
replace this paragraph with the tested private reporting URL or an owner-controlled security
contact and fallback. Do not invent a mailbox or publish sensitive details through an ordinary issue.

Include the affected version/commit, deployment and architecture, minimal synthetic reproduction,
impact and any workaround. Do not send real documents, passwords, tokens or recovery keys. Avoid
testing systems you do not own or have permission to assess. Coordinate disclosure through the
approved private channel once available. Ordinary bugs belong in GitHub issues.

## Security boundaries

The web administrator has no implicit read permission to another user's personal folder. The
host administrator, Proxmox administrator, storage and backup operators can access plaintext
files and state; Confidential is an app access lock, not encryption at rest or E2E encryption.
Protect the host, disk and backups independently. Normal files may leave the host when cloud AI
is explicitly enabled. MCP clients may forward received data to their own providers.

No public release exists yet. The intended first release is beta. The owner must approve the
supported deployment/version matrix and vulnerability maintenance expectations before publication.
Known risks and coverage gaps are recorded in the release packet; ADR-016 is accepted and
unimplemented. Internal reviews and automated tests are not an external security audit.
