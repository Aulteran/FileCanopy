> Owner decision 2026-11-04: Docker is the first-beta priority. Native LXC is deferred until later; this is a future validation plan, not a first-beta gate or implemented support.

# Native Proxmox helper-script LXC — release validation plan

**Owner priority, not yet implemented or supported.** On 2026-11-03 the owner clarified native
application installation inside an LXC, rather than Docker nested inside an LXC. No Proxmox host
is connected here. No helper script has been submitted or accepted upstream.

## What must be prepared and tested

1. Pin a Proxmox version, host/kernel architecture, unprivileged guest template/version, storage
   layout and helper-script revision. Record actual tested resources; do not invent minimum RAM/CPU.
2. Design native service units for PostgreSQL + pgvector, migrations, the API, nginx/static web,
   optional workers/Redis replacement if needed, sealed Office/OCR services and their sockets.
   Reuse the application's dependency pins, privacy gates, startup migration ordering, log
   redaction, non-root UIDs and read-only/write-path boundaries. This is additional packaging work;
   the repository currently contains Dockerfiles and Compose, not these service definitions.
3. Test native Linux resolver device/mount/symlink safeguards on the actual LXC backing storage.
   Record guest-to-host UID/GID mappings, bind mount ownership, root confinement and how library,
   app-data, DB and secrets are backed up. Never disable ACL/privacy or parser seccomp/resource
   restrictions simply to make an LXC installation pass. Host root remains outside app trust.
4. Run fresh/no-AI setup, two-user isolation/share/revoke, Confidential idle lock, real generated
   Office/PDF/OCR fixtures, upload/download, restart/reboot, older-schema upgrade and destroyed-guest
   restore. Include failure/recovery, read-only service paths and canary-free logs.
5. Re-audit exact guest packages and native vendored libraries; supply licences/source/notices.
   Document update/rollback/uninstall behaviour (uninstall must preserve user data by default).
6. Have the owner approve that tested matrix and helper content. New helper scripts currently go
   to the community project's **DevScripts** testing repository first. Submission, contacting its
   maintainers or claiming upstream availability requires a separate explicit instruction.

The community-script ecosystem and Proxmox are separate projects, not FileCanopy affiliates.
Do not describe the planned helper as an existing one-click installer. The Docker install remains
the available deployment path while native packaging and this real-host validation are pending.

## Authoritative references

Accessed 2026-11-03:

- [Proxmox container documentation source](https://github.com/proxmox/pve-docs/blob/master/pct.adoc):
  Linux system-container model, security and features; current guidance distinguishes nested
  application containers and VM isolation. It is not evidence that FileCanopy native services work.
- [Community helper contribution rules](https://github.com/community-scripts/ProxmoxVE/blob/main/CONTRIBUTING.md):
  new-script testing/submission route; inspect again before any future submission.
- [DevScripts](https://github.com/community-scripts/DevScripts): testing project, not proof of
  FileCanopy inclusion or upstream endorsement.
