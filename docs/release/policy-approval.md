# Policy approval package — 2026-11-04

**Prepared for review; no root policies adopted or repository settings changed.**
Exact proposed documents: [CONTRIBUTING](drafts/CONTRIBUTING.md),
[SECURITY](drafts/SECURITY.md), [CHANGELOG](drafts/CHANGELOG.md).
The M24 acceptance plan requires owner approval before promotion. Public beta
licensing and other release gates remain independent of policy approval.

## Decisions requested

| ID | Concrete proposal for owner approval | Alternative / exact information needed |
|---|---|---|
| P1 — private reporting | Use GitHub private vulnerability reporting at `https://github.com/Aulteran/FileCanopy/security/advisories/new`. Authorize enabling it only after assigning the repository owner/maintainer who monitors reports and notifications. GitHub accounts are required for this route. | If GitHub-only reporting is unsuitable, supply an actual monitored owner-controlled alternative and permission to publish it. No mailbox or fallback recipient has been invented. |
| P2 — beta maintenance | Use the exact SECURITY draft wording: fixes target the latest published beta; older betas have no promised backports; users may need to upgrade; no stable-support window or response deadline. Releases document migrations, backup/restore steps and known risks. | Choose a different maintained-version range/capacity only if someone accepts responsibility for it. Name the responsible maintainer in the approval record, without requiring personal contact details in the public policy. |
| P3 — policy text | Adopt all three linked drafts, including GitHub issues for ordinary bugs, synthetic/sanitized reports, AGPL contributions with preserved third-party terms, no new CLA/DCO, and an Unreleased changelog with no invented beta number/date. | Identify exact wording changes. No bounty, safe-harbour, audit certification or response-time promise is introduced. |
| P4 — source/Redis follow-up | Approve the reviewed separate Valkey replacement recommendation or request specialist review of retaining Redis 7.4.12. Choose source-download ownership/location before publication. | This is dependency/publication preparation, not approval to replace Redis now, publish images or waive native/font source review. See [licensing report](licensing-review-2026-11-04.md) and [source queue](source-delivery.md). |

P1–P3 form the policy adoption decision. P4 remains a separate licensing/release
decision. No reply is inferred from elapsed time or from approval of PR #32.

## Reporting-state evidence and activation checklist

Read-only GitHub REST inspection on 2026-11-04:

```text
GET /repos/Aulteran/FileCanopy/private-vulnerability-reporting
{"enabled":false}
GET /repos/Aulteran/FileCanopy
visibility=public, default_branch=main
```

[Retained response](evidence/licensing-2026-11-04/private-reporting.json).
The endpoint returned a successful explicit false; this is not a 404 interpreted
as disabled. [GitHub's configuration documentation](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository)
allows owners/admins of public repositories to enable the feature. Eligibility
and the setting are established; an actual reporter/maintainer workflow is **not
verified** while disabled. No advisory was created and no third party contacted.

After explicit P1/P2 approval:

1. Record approving owner, responsible maintainer(s), chosen maintenance scope and
   the exact draft revision. Check who has report access and receives notifications.
2. An authorized administrator enables **Private vulnerability reporting** in the
   repository's Settings → Advanced Security. A fresh REST GET must return true.
3. From an ordinary signed-in account without repository write access, check that
   Security → Advisories shows **Report a vulnerability** and the proposed URL
   opens a private report form. Do not submit real vulnerability details as a probe.
4. With separately approved synthetic test submission, verify the designated
   maintainer receives it, can reply privately, and the reporter sees the reply.
   Ensure it is not listed in public issues/advisories. Record date, roles,
   result and private-access evidence without publishing report content. Do not
   create a public advisory to test this route. No response-time promise is required.
5. Decide whether GitHub account access is an acceptable reporting limitation.
   If an alternative is required, verify it receives/replies privately before
   adding it to SECURITY. Never direct exploit details into a public issue when
   a route is unavailable.
6. Only after approval and these checks, promote the drafts to root as below.
   Recheck reporter access and notifications before each beta publication. If the
   route breaks, repair the contact policy before claiming it is available.

## Exact adoption transformation (not performed)

Copy each draft to the matching root filename. Change its first heading to
`# Contributing to FileCanopy`, `# Security policy`, or `# Changelog` respectively.
Remove the explicitly marked **Draft status** paragraph(s), leaving proposed
policy content below them. In CONTRIBUTING, change `SECURITY.md` to a root-relative
link; in SECURITY/CHANGELOG, change `../README.md` to `docs/release/README.md` and
`../known-issues.md` to `docs/release/known-issues.md`. Do not add a version or release
date to the Unreleased changelog during policy adoption. Replace the SECURITY draft
state evidence with the tested route verification record in this approval packet,
not a claim in the policy about tests that never happened.

Review the resulting root diff, run documentation-link/secret checks and record
approval plus route-verification evidence here. Promotion is a local review step;
push/PR/merge, repository setting changes and release publication retain their
own authorization boundaries. This session has no permission to push or open a PR.
