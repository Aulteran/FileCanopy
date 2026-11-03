# Contributing to FileCanopy — draft for owner approval

Not an adopted project policy. After approval, promote this file to root `CONTRIBUTING.md` and
adjust relative links. FileCanopy's first public release is intended to be beta; no stable-support
promise or response-time commitment is made here.

## Bugs and ideas

Report ordinary bugs through [GitHub issues](https://github.com/Aulteran/FileCanopy/issues).
Include the image tag and full image ID (or Git commit), deployment method, CPU architecture,
host/guest and Docker/Compose versions, reproduction steps, expected/actual behaviour and only
sanitised logs. State whether AI, OCR, email or MCP is enabled. Use synthetic files. Never attach
real personal documents, provider keys, setup/invite/reset links or an unredacted `.env`.

Discuss substantial changes in an issue first. Work one milestone or tested subtask at a time.
For suspected vulnerabilities, follow the approved SECURITY policy; a private route must be
configured and verified before this draft is adopted. Do not post exploitation details publicly.

## Development

Read `CLAUDE.md`, `PROJECT_STATUS.md`, `MILESTONES.json`, the relevant plan and security/storage
specifications. Node.js 22+ and Docker are used by the check harness. Use `npm ci` and
`npm ci --prefix apps/web`. Run `npm run check`, `npm run check:web`, `npm run check:api`,
`npm run test:compose` and `npm run test:e2e` as appropriate; changes to isolation or release
packaging need their real integration checks. Record commands, results and skips in the PR.

Keep portable files authoritative; enforce central ACL/privacy policy everywhere; never send
Private/Confidential data to cloud AI. Tests use synthetic fixtures, no paid live API calls.
Add meaningful regression coverage for behaviour changes. Document migrations and operational
impact, and update milestone evidence and known limitations. Cross-cutting product changes need
an approved decision record. Do not claim an untested platform is supported.

## Licensing and review

The project declares `AGPL-3.0-only`; third-party components keep their own licences. Submit only
material you have the right to contribute under the project's terms, preserve attribution and
record a dependency's exact version, source and licence when adding or changing it. No CLA,
copyright assignment or DCO requirement is introduced by this draft. Maintainer review is
required; contributors do not publish images or releases as part of an ordinary change.
