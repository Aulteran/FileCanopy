# Exact-build notices and source delivery

Prepared 2026-11-04. **Not an adopted written source offer and not clearance.**
Prefer delivering corresponding source with the binary download, avoiding an
unfunded multi-year offer. The owner must approve an accessible release location
and person responsible for retaining it before publication. An upstream link can
be useful evidence without satisfying FileCanopy's own delivery responsibilities.

## Materials already assembled

| Material | Location / current verification boundary |
|---|---|
| Six offline notice bundles and package→source mappings | [Inspection index](evidence/licensing-2026-11-04/inspection.json); archive SHA-256/size recorded. Debian common licence texts and copyright symlinks are included. Empty Redis notice listing is explicit. These are inspection artifacts, not a final release notice bundle. |
| Redis 7.4.12 full terms | [Retained text](evidence/licensing-2026-11-04/upstream/redis-7.4.12-LICENSE.txt), no licence choice asserted. Must accompany any redistribution if retained under reviewed terms. |
| Liberation Sans 1.07.4 | [Editable source archive](../../apps/web/public/sources/liberation-1.07.4-dbeb786b.tar.gz) and [build/provenance guidance](../../apps/web/public/sources/README.md), copied through Vite's public assets into nginx. Full GPL/exception text stays beside binary fonts. Source is upstream release commit dbeb786b; no byte-identical font rebuild claimed. |
| certifi 2026.10.23 | [Exact PyPI source archive](../../apps/api/vendor-sources/certifi-2026.10.23.tar.gz); included by API Dockerfile's existing `COPY apps/api apps/api`. Archive includes source/CA bundle and MPL text. No root-policy adoption needed to preserve third-party rights. |
| pip 26.2.2 and its certifi 2026.10.19 copy | [Exact pip source archive](../../apps/api/vendor-sources/pip-26.2.2.tar.gz), PyPI hash verified; includes vendor sources/modifications/manifest and licences. All installed vendor files byte-compared with the archive. |
| Native notices | [API notice](../../apps/api/THIRD_PARTY_NOTICES.md), LGPL usage/reverse-engineering statement; LGPL wheel text, added GPL v3 text and exact libpq 18.6/OpenSSL 3.5.8/4.0.3 notices. Other native terms remain incomplete. |
| Wrapper source locators | [Upstream manifest](evidence/licensing-2026-11-04/upstream/sources.json) records exact PyPI sdist URL/hash/size for psycopg 3.3.6, cryptography 50.0.2, Pillow 13.0.0; **locators only**, not retained source. psycopg-binary has no sdist in that response. |

## Completion queue for the release candidate

| Owner / reviewer action | Required material and acceptance evidence |
|---|---|
| Release operator: freeze inputs | Reviewed clean commit, lockfiles/wheel filenames+hashes, base digests, platform, exact apt/apk binary+source versions, OCR language arguments, Dockerfiles and all build recipes. Source commit tags alone do not freeze live package repositories. |
| Release operator: native closure | For every `.so` hash and static component, record exact upstream release/commit, recipe, flags, patches and licence choice. Start with libpq/OpenSSL, psycopg's Kerberos/LDAP/SASL/keyutils/crypt/SELinux dependencies, cryptography's Cargo.lock/crates and Pillow's bundled codecs. Obtain the wheel build recipes for the exact wheel; do not substitute the current default branch. |
| Licensing reviewer: LGPL | Confirm section 4 route. Supply minimal corresponding library source plus application code/rebuild materials, or demonstrate a suitable shared-library replacement mechanism that actually operates with a modified compatible library. Include GPL and LGPL texts, notices and any required installation information. Record a real modified-library rebuild/replacement test; a successful ordinary database connection is insufficient. See [LGPL v3 section 4](https://www.gnu.org/licenses/lgpl-3.0.html). |
| Release operator: Debian source | From each `*-packages.txt`, deduplicate source name+source version (columns 3/4). Use a matching signed Debian source index/snapshot to obtain `.dsc`, orig tarball(s), Debian patch tarball/diff and build scripts, with hashes. `+b1` binary rebuild suffix need not be the source version. Preserve `debian/copyright` and referenced common licence texts. Record missing snapshot files as blockers, not as resolved URLs. |
| Release operator: Alpine source | Parse `P/V/o/L` records (name/version/origin/licence) in each raw apk database. Retrieve the matching aports recipe commit, patches, checksummed upstream inputs and relevant build configuration. Include busybox/musl/compiler runtime exception text. Licence strings in apk are not the actual notice bundle. |
| Release operator: compiled bases | Retain exact CPython, nginx, PostgreSQL and Redis/approved alternative source/build recipes; these are not fully represented by apt/apk. pgvector's exact 0.8.1 tarball/checksum and notice are already specified by its Dockerfile; retain the tarball with release sources too. |
| Licensing reviewer: fonts/assets | Confirm font source corresponds to the four shipped TTFs and GPL section 3 same-place delivery is adequate for the release channels. Review aggregation separately from linking. Establish provenance/subsetting/reserved-name treatment for repository WOFF2s. Retain all Adobe/Foxit/codec/ICC notices. |
| Release operator: pip and source distribution | Preserve pip's exact source archive/vendor notices and repeat installed-vendor byte comparison on the final candidate. Review any builder/cache archives actually offered. Local-only build tools need not be called distributed runtime dependencies. |
| Owner: source hosting/retention | Select an owner-controlled download location with source next to exact-version binaries, not a mutable `main` URL. Assign retention/access responsibility. If choosing a written offer instead, specialist must specify recipients, duration, delivery medium/cost and means of fulfilment for each applicable licence; this packet makes no such offer. |

For GPL v2 object code use the exact licence's section 3 requirements; equivalent
access from the same place is addressed in its final paragraph. For GPL/AGPL v3
network binary distribution review section 6(d); review AGPL section 13 for users
interacting with modified network software. Include complete application source,
dependency/build/install scripts and any required source access in the final app.
For certifi, preserve MPL source/notice access as described in
[MPL 2.0 section 3](https://www.mozilla.org/en-US/MPL/2.0/). No claim is made that
FileCanopy must relicense separately aggregated container packages under AGPL.

## Reproduce inspection and delivery checks

Run from the checkout, using exact image IDs/refs in
`evidence/licensing-2026-11-04/inspection.json`:

```text
node scripts/licensing-inspect.mjs OUTPUT API WEB POSTGRES REDIS CONVERTER OCR
node scripts/licensing-fetch.mjs OUTPUT/upstream
node scripts/licensing-noassertion.mjs
node scripts/licensing-server-probe.mjs VALKEY_IMAGE API_IMAGE OUTPUT/valkey.json
npm --prefix apps/web run build
node scripts/licensing-verify.mjs API_IMAGE OUTPUT/verification.json WEB_IMAGE
```

Inspection uses read-only containers with no network. Fetch accesses upstream
sources; it does not publish. Server probe creates only disposable internal-network
containers. Verification checks source hashes, certifi byte correspondence, source
archive contents and exact notice copying. It is not a legal-clearance test.
Git attributes disable text conversion for the new hashed evidence and native
notice files. Raw apk database blank record separators are intentionally retained;
the scoped whitespace attribute preserves those bytes without hiding other diff checks.
Fetch retains historical 404s for the obsolete font URLs as well as the recovered
source; those historical failures do not mean the replacement source is absent.

For the final candidate, assemble a recipient-facing `NOTICES` index mapping
component/version/image/path to full texts and source materials; archive it with
SBOMs, image digests and source checksums. Publish only after separate authorization.
Then verify from an ordinary recipient account that source downloads work, unpack,
match the running versions and include patches/build scripts. Test a source build
and LGPL replacement route. Keep evidence of both successes and remaining gaps.

This packet does not supply the full Debian/Alpine/native corresponding source yet.
Do not describe the inspection tarballs, scanner SBOMs, or the two added source
archives as a complete source bundle for all images.

Note (2026-11-05): duplicate copies of the certifi, pip and Liberation archives under `evidence/licensing-2026-11-04/upstream/` were removed to avoid repository bloat. The shipped copies above remain, and exact URLs, sizes and SHA-256 values stay in [sources.json](evidence/licensing-2026-11-04/upstream/sources.json).
