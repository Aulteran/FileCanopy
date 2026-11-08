# Roadmap after 1.0.0

Items parked until after the first stable release, 1.0.0. This is a parking list, not a commitment or a schedule. Nothing here is implemented or promised, and listing an item does not decide that it is acceptable for beta.

The source documents stay authoritative: [known issues](release/known-issues.md), [open decisions](11_OPEN_DECISIONS.md), [current status](PROJECT_STATUS.md), [what is built](roadmap.md), and the milestone and ADR files they link. Update this list when those change.

## Platforms and packaging

- ARM64 (`linux/arm64`) images: removed from the first beta and 1.0.0 publish; return only with real build and smoke-test evidence ([status](PROJECT_STATUS.md)).
- Native Proxmox helper-script LXC: needs native service packaging and real-host tests ([plan](release/proxmox-lxc.md)).
- Container registries beyond GHCR: parked.
- Self-hosted GitHub Actions runner: parked.
- Byte-identical (reproducible) rebuilds and signed images: not established ([known issues](release/known-issues.md)).
- Multi-node Docker Swarm: only single-node is tested ([known issues](release/known-issues.md)).
- Registry pulls against a public release: not yet tested ([known issues](release/known-issues.md)).
- Optional Compose services: possible split of the optional services, and removal of the Redis and worker profile ([status](PROJECT_STATUS.md)).

## Licensing and naming

- Redis 7.4.12 licence review (RSALv2/SSPLv1 terms), or replacement; Valkey 9.0.6 has passed tested compatibility probes but a replacement needs its own decision ([licensing review](release/licensing-review-2026-11-04.md)).
- PDF.js Liberation font aggregation and native/OS source and notice obligations, with specialist review ([known issues](release/known-issues.md)).
- External name clearance for FileCanopy: waived for M24 with no legal clearance claimed; the name may change ([status](PROJECT_STATUS.md)).

## Sign-in, accounts and API

- Public or third-party API and token sign-in: options are being written in a separate thread and are not on `dev` yet, so no link is given here.
- Real Google sign-in testing with a live Google account ([guide](guides/google-sign-in.md)).
- Whole-session idle auto-lock: deferred; only the Confidential folder lock exists ([known issues](release/known-issues.md)).
- Rate limits and the AI breaker are per process; review before running more than one API process ([known issues](release/known-issues.md)).

## Backups

- Per-root backup keys (ADR-016, M25): independent security review and the M25 low-severity follow-ups ([M25](milestones/M25-design-and-plan.md)).
- Dedicated escrow key for owner recovery; escrow is sealed to the shared-folder administrator key today ([known issues](release/known-issues.md)).
- Backup jobs (ADR-021, M27): independent security review and the M27 low-severity follow-ups ([M27](milestones/M27-design-and-plan.md)).
- Backup job destinations: FTP and FTPS are not built; remote free space is not checked; failed remote uploads restart from the beginning ([ADR-021](adr/ADR-021-backup-jobs-and-destinations.md)).
- Remote job dependencies `paramiko` (LGPL-2.1) and `boto3` need licence notices before publication ([known issues](release/known-issues.md)).
- Purge of backups for frozen roots and deleted accounts, with an audited, confirmed trigger ([open decision 16](11_OPEN_DECISIONS.md)).
- Asynchronous restore with progress and cancellation ([open decision 17](11_OPEN_DECISIONS.md)).
- Granular single-file restore UI ([known issues](release/known-issues.md)).

## Privacy, mail and security assurance

- Scrub names from quarantine metadata for already-disabled senders (M13/M14) ([known issues](release/known-issues.md)).
- Held-mail reprocessing and optional SMTP administrator notices ([known issues](release/known-issues.md)).
- Parser sandbox: restrict `kill`, `tkill` and `prlimit64` to the child's own process ID (M10) ([M10](milestones/M10-design-and-plan.md)).
- Legacy `.doc`, `.xls` and `.ppt` macro scanning ([M10](milestones/M10-design-and-plan.md)).
- Concurrency tests: overlapping move, share and trash, and PostgreSQL proposal and label interleavings ([known issues](release/known-issues.md)).
- External security audit: none has been done ([known issues](release/known-issues.md)).
- Confidential local AI indexing after unlock, with a short-lived job token ([open decision 12](11_OPEN_DECISIONS.md)).

## Files, viewing and search

- Paperless-style PDF viewer comparison and viewer key tests ([status](PROJECT_STATUS.md)).
- First-run order: AI, users and SMTP before file setup, partly addressed by the server settings step ([status](PROJECT_STATUS.md)).
- Document viewer follow-ups: page navigation, text selection, audio and video on the Range endpoint, in-app spreadsheet grid ([status](PROJECT_STATUS.md)).
- PDF splitting: blank-page check via paint operators (M17), thumbnail review, bookmark remap, background proposals ([M17](milestones/M17-design-and-plan.md), [known issues](release/known-issues.md)).
- Collections: examines at most 2,000 members; no bulk add or add-to-existing suggestions ([known issues](release/known-issues.md)).
- Search: shared-tree files indexed through the oldest active administrator only; `/search` has no throttle ([status](PROJECT_STATUS.md)).
- Performance: `filter_visible` does linear work per id; no PostgreSQL performance guarantee ([known issues](release/known-issues.md)).
- Mounted drives as managed folders: not supported until after the first full release ([open decisions](11_OPEN_DECISIONS.md)).
- Bulk-import command-line tool, resumable and token-authenticated ([status](PROJECT_STATUS.md)).
- Full file versions, multi-device offline changes and conflict merging ([open decisions](11_OPEN_DECISIONS.md)).
- Sync clients and WebDAV ([open decisions](11_OPEN_DECISIONS.md)).
- Personal-folder end-to-end encryption ([open decisions](11_OPEN_DECISIONS.md)).
- Per-file external anonymous link sharing ([open decisions](11_OPEN_DECISIONS.md)).
- Mobile scanner app and advanced photo OCR or multimodal indexing ([open decisions](11_OPEN_DECISIONS.md)).
- Linking duplicates: next to "Move to trash", offer to keep one real copy and have the other folders reference it (a symlink or reference, as the file-system design allows). Example: the same passport copy sits in the 2022 and 2024 visa-renewal folders and should exist once. Open design questions: how permissions work when folders have different access, how backups and restores handle the reference, and what happens to the other locations when the real copy is deleted or moved. Not designed or implemented.
- Collection timelines, notes and automatic summaries ([open decisions](11_OPEN_DECISIONS.md)).

## AI and MCP

- OpenAI and Gemini model price suggestions.
- AI confidence thresholds, tuned on a synthetic corpus with measured false positives ([open decision 7](11_OPEN_DECISIONS.md)).
- Background AI organization proposals and Private local proposals; reclaim audit for stuck applying states; empty-folder undo cleanup ([known issues](release/known-issues.md)).
- Document chat: multi-turn conversations, page citations, and totals computed rather than quoted ([known issues](release/known-issues.md)).
- Embedding benchmarks on the target CPU-only hardware ([open decision 14](11_OPEN_DECISIONS.md)).
- MCP: OAuth, SSE, write tools and opt-in access to protected files ([known issues](release/known-issues.md)).
- AI folder cleanup suggestions across very large existing trees ([open decisions](11_OPEN_DECISIONS.md)).

## Accessibility, documentation and UX

- Full WCAG conformance review; the current checks are structural, keyboard and selected axe checks only ([known issues](release/known-issues.md)).
- API reference documentation: not written.
- Deployment matrix: publish only an evidence-backed matrix after owner approval ([known issues](release/known-issues.md)).
