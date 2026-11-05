# Contributing to FileCanopy — draft for owner approval

**Draft status:** Not adopted. Owner approval and verified private vulnerability
reporting are required before promotion to root `CONTRIBUTING.md`; see the
[approval package](../policy-approval.md). Proposed wording follows.

## Bugs and ideas

Report ordinary bugs through [GitHub issues](https://github.com/Aulteran/FileCanopy/issues).
Include the image tag and full image ID (or Git commit), deployment method, CPU architecture,
host/guest and Docker/Compose versions, reproduction steps, expected/actual behaviour and only
sanitised logs. State whether AI, OCR, email or MCP is enabled. Use synthetic files. Never attach
real personal documents, provider keys, setup/invite/reset links or an unredacted `.env`.

Discuss substantial changes in an issue first. Work one milestone or tested subtask at a time.
For suspected vulnerabilities, follow [the security policy](../../../SECURITY.md).
Do not post exploitation details publicly. The beta has no stable-support or
response-time promise; proposed maintenance expectations are in that policy.

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
record a dependency's exact version, source, licence and shipped notices when adding or changing it.
Distinguish application runtime, browser assets, native/vendor code and build-only
tools. Explain source-delivery requirements and preserve separately licensed
assets; a clean scanner result is not licensing sign-off. Include operational and
migration impact in dependency replacement proposals before making substantial changes. No CLA,
copyright assignment or DCO requirement is introduced by this draft. Maintainer review is
required; contributors do not publish images or releases as part of an ordinary change.

Pull requests should state the problem, resulting behaviour, checks run (including
skips) and remaining risks. Do not submit secrets or personal data in code, test
fixtures, screenshots or logs. Beta maintenance is limited by maintainer capacity;
opening an issue or pull request does not create a response deadline.
