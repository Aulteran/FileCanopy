# Beta limitations and open follow-ups

This consolidates release-relevant limits; the milestone design documents and PROJECT_STATUS
retain the historical detail. Listing an issue here does not decide that it is acceptable for beta.

| Area | Limitation / evidence | Release action |
|---|---|---|
| Licensing | Redis 7.4.12 uses RSALv2 or SSPLv1; PDF.js ships GPL v2 Liberation fonts with exceptions; native bundled/source obligations unresolved | Resolve or replace with a reviewed, tested alternative before licence clearance; see [audit](dependency-audit.md). |
| Packaging | Deterministic commit tags prepared; live apt/apk packages, unhashed Python downloads, toolchain and filesystem timestamps prevent a demonstrated byte-rebuild claim; no signed images. Six local-image scanner SBOMs are recorded/validated, with NOASSERTIONs outstanding. | Freeze inputs, review unknowns, retain final candidate SBOMs/source/notices and compare isolated rebuilds. |
| Native Proxmox LXC | Owner priority, no native installer or real-host evidence; app depends on Linux filesystem and parser isolation facilities | Follow [platform validation plan](proxmox-lxc.md); do not advertise support yet. |
| Backup keys | ADR-016 accepted, **unimplemented**; [M25 planned, not started](../milestones/M25-design-and-plan.md): one recovery key opens all archives in its chain; no owner-only self-restore keys | Rotate after sharing; keep archives private. Owner decides beta acceptance; do not advertise per-root keys. |
| Backup/restore | Frozen/deleted roots never pruned; synchronous restore; no granular file restore UI; disaster backups are full copies and contain all files/state/secrets | Document capacity and recovery limits; exercise real restore. |
| Parser sandbox | M10 signal/resource syscall own-pid restriction follow-up (`kill`/`tkill`/`prlimit64`) remains open | Preserve sandbox and documented risk; no unreviewed LXC weakening. |
| Performance | `filter_visible` linear per-id work; SQLite developer benchmarks, no PostgreSQL performance guarantee | Measure realistic deployments; no scale promise. |
| Sessions/rate limits | Whole-session idle auto-lock deferred; Confidential folder idle lock exists; API rate limits and AI breaker are process-local | One API process in current deployment; review before replication. |
| Concurrency tests | Mixed overlapping move/share/trash and concurrent PostgreSQL proposal/label HTTP interleavings remain untested | M23 completion is bounded; track follow-up tests. |
| Accessibility | Structural/keyboard smoke and selected axe checks, not full WCAG certification or all-screen pixel review | No comprehensive accessibility conformance claim. |
| Mail | Already-disabled sender quarantine metadata scrub follow-up; optional SMTP administrator notices and reprocessing held mail deferred | Preserve privacy caveat; see M14 guide/plan. |
| AI organisation | Background proposals, Private local proposals, stuck-applying reclaim audit/scrub and empty-folder undo cleanup follow-ups | No automatic capability claims beyond implemented validated rename/move flow. Historical proposal scrub/storage race fixes are done in M23. |
| PDF split/duplicates | Blank-page heuristic, thumbnail review, bookmark remap, background proposals and dismissal follow-ups | Preserve source and approval workflow. |
| Collections | At most 2,000 sorted members examined; hidden entries may crowd visible ones (per-adder cap helps); bulk/add-to-existing suggestions deferred | Do not promise unlimited collections or results. MCP list_collections is implemented in M21. |
| Chat | Single-turn, file/excerpt citations without page numbers; totals quoted, not computed | No page citation, calculator or multi-turn claims. |
| MCP | No OAuth, SSE, write tools or protected-file opt-in; rate limits per process | Normal-only, scoped, remote-AI acknowledgement. Credential/token revocation and valid-token shared-IP fixes are done in M23. |
| Deployment | Swarm only tested single-node; registry pulls not tested against a public release; ARM64 and native LXC not validated | Publish only an evidence-backed matrix after owner approval. |
| Trust/privacy | Plaintext host/storage/backup access; cloud transmissions cannot be recalled; no external security audit | Disclose plainly; no host-confidentiality or compliance guarantee. |

Baseline: [M23 closure](../milestones/M23-design-and-plan.md),
[open decisions](../../11_OPEN_DECISIONS.md), [current status](../../PROJECT_STATUS.md).
