# Security policy — draft for owner approval

**Draft status:** Not adopted. GitHub private vulnerability reporting is available
for this public repository but **disabled**, confirmed by REST GET on 2026-11-04
(`{"enabled":false}`). The proposed policy below may be promoted only after owner
approval, enablement and reporter/maintainer verification in the
[approval package](../policy-approval.md). It is not an active reporting route yet.

## Reporting a suspected vulnerability

Use [GitHub private vulnerability reporting](https://github.com/Aulteran/FileCanopy/security/advisories/new)
to report a suspected vulnerability privately. A GitHub account is required.
Keep exploit details, sensitive logs and affected documents out of public issues.
If the form is unavailable, do not post those details publicly; wait for a verified
private route in this policy. No alternative mailbox is currently designated.

Include the affected version/commit, deployment and architecture, minimal synthetic reproduction,
impact and any workaround. Do not send real documents, passwords, tokens or recovery keys. Avoid
testing systems you do not own or have permission to assess. Coordinate disclosure through the
private channel. Ordinary bugs belong in
[GitHub issues](https://github.com/Aulteran/FileCanopy/issues).

## Beta maintenance

The first public release is intended to be beta. Security fixes target the latest
published beta; backports to earlier betas are not promised. Users may need to
upgrade to receive a fix. Release notes will identify migration steps, backup and
restore precautions, and known risks. Until a beta is published, there is no
released version to maintain under this policy.

Reports are reviewed as maintainer capacity permits. No acknowledgement deadline,
fix deadline, stable-support window, bounty or safe-harbour commitment is offered.
Disclosures and fixes are coordinated privately with the reporter before public
release of sensitive details when practicable.

## Security boundaries

The web administrator has no implicit read permission to another user's personal folder. The
host administrator, Proxmox administrator, storage and backup operators can access plaintext
files and state; Confidential is an app access lock, not encryption at rest or E2E encryption.
Protect the host, disk and backups independently. Normal files may leave the host when cloud AI
is explicitly enabled. MCP clients may forward received data to their own providers.

An evidence-backed deployment/version matrix must accompany the beta; a platform
priority is not a support guarantee. [Release preparation](../README.md) and
[known issues](../known-issues.md) record risks and coverage gaps, including the
accepted but unimplemented ADR-016 backup-key design. Internal reviews and
automated tests are not an external security audit or compliance certification.
