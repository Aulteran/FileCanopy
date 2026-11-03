# Runtime dependency licence audit — 2026-11-03

**Inventory performed; compatibility/redistribution clearance is not complete.** This is a
technical evidence register and issue assessment, not legal advice. Do not equate an SPDX field,
package name, an optional service or a successful build with clearance to distribute it.

## Inputs and reproducible evidence

- [Manifest hashes](evidence/manifest-hashes.json): root and web package manifests/lockfiles,
  API runtime pins and dev requirements. Root npm has only development tooling. API requirements
  are 34 exact pins, not a hash-locked resolver output. No separate Python lockfile exists.
- [Dependency register](dependency-register.md): **34 pinned Python dependencies**, plus pip
  shipped in the runtime image; **16 npm production-closure lock entries**, including optional
  native canvas variants and scheduler; **167 build/test lock entries** separately labelled.
  Each has an exact version, declared licence and source/evidence reference. Optional platform
  entries are inventoried, not asserted to ship in the browser bundle.
- [Python artifact snapshot](evidence/python-runtime.json): exact image ID/platform, METADATA
  hashes, dependency declarations, licence text paths, SHA-256 and full installed texts. All
  pinned versions matched. pip's bundled notices are preserved too. Distribution metadata is a
  primary artifact declaration; it does not alone prove all native transitive components covered.
- [npm lock snapshot](evidence/npm-locks.json): version, licence field, resolved artifact URL,
  integrity, scope and optional OS/CPU conditions for every lock entry. Upstream version URLs
  identify the packages; they were not each independently fetched. Local installed React/PDF.js
  notices were inspected; optional platform binaries not installed here remain lockfile evidence.
- [Bundled notices](evidence/bundled-notices.json): 16 exact licence files with text and hashes,
  including PDF.js codecs/fonts/CMaps/ICC, React/React DOM/scheduler and self-hosted fonts.
  [Binary asset hashes](evidence/binary-assets.json) identify the shipped font/decoder/CMap/ICC
  assets; unversioned repository fonts are recorded by hash rather than an invented version.
- [Container register](container-register.md), [image identities/package databases](evidence/container-inventory.json)
  and [compressed Debian copyright texts](evidence/container-copyrights.json.gz): API, nginx web,
  PostgreSQL/pgvector, Redis, converter and OCR, all inspected as Linux amd64 images. The API and
  core images were rebuilt locally with M24 notice changes, identified by immutable image IDs,
  not claimed to be the final clean-commit release. Converter/OCR were also built during this session. Each installed OS
  package/version is recorded; NOASSERTION and raw licence labels are unresolved until reviewed.
- [Application CycloneDX inventory](evidence/application.cdx.json): application packages only,
  **not a complete container SBOM**. It has no claim of vulnerability or compatibility clearance.
- [Six Docker Scout image SBOMs](evidence/sbom-index.json): compressed SPDX 2.3 documents for the
  exact six rebuilt/local image IDs; 248/100/80/31/503/350 scanner package records respectively
  (including scanner relationships/image components, not just package-manager counts).
  [Validation evidence](evidence/sbom-validation.json) checks the official SPDX schema, image
  identity, package counts and SHA-256. Declared-licence NOASSERTION counts are 46/7/11/4/103/73.
  Schema validity does not prove completeness or compatibility. All uncertain/native/source cases
  still need review, and final clean-candidate image SBOMs must be regenerated.

Regenerate with `npm ci --prefix apps/web`, `npm run release:audit -- <api-image>`, then
`node scripts/image-inventory.mjs <api> <web> <postgres> <redis-digest> <converter> <ocr>`.
Containers run with no network for inspection. Generated files record access date and exact IDs.
Regeneration replaces snapshots; review changes and rerun the audit after any manifest/image change.
`node scripts/container-sbom.mjs` uses installed Docker Scout and explicit `local://<image-id>`
sources; `node scripts/validate-sboms.mjs` validates its output using the Ajv version locked with
the repository's ESLint tooling. The initial Scout run warned about a Windows temporary-file
cleanup lock for Redis but returned success and its saved document passed validation. This is
inventory/schema work, not a vulnerability scan or legal analysis.

## Findings and disposition

| ID | Confirmed evidence | Assessment / unresolved action |
|---|---|---|
| L1 | LICENSE contains the full AGPL v3 text. Both package manifests and lock roots declare AGPL-3.0-only; README agrees. | Application declaration is consistent. Third-party components are not relicensed. Owner's final publication sign-off remains conditional on these audit findings. |
| L2 | Most Python requirements declare MIT/BSD/Apache/PSF-family terms; exact fields and texts are in the register. `cryptography` 50.0.2 declares Apache-2.0 OR BSD-3-Clause; `cffi` 2.1.1 MIT-0; Pillow 13.0.0 MIT-CMU. | No incompatibility established from these top-level texts; retain notices and review all bundled native terms. This is not a blanket compatibility conclusion. |
| L3 | `psycopg` and `psycopg-binary` 3.3.6 declare LGPL-3.0-only; upstream documents that binary wheels package client libraries. | Review exact bundled libpq/OpenSSL and other library versions, corresponding source and LGPL relinking/replacement obligations for the distributed wheel/image. Do not treat the LGPL label as automatic clearance or an automatic incompatibility. |
| L4 | `certifi` 2026.10.23 declares MPL-2.0; installed text preserved. | File-level source/notice obligations need fulfilment for the exact distributed copy. Mozilla's FAQ describes larger-work distribution but is not a substitute for the licence. |
| L5 | Python package `redis` 8.1.0 declares MIT. **Redis server** in the pinned Compose digest runs **7.4.12**, with RSALv2 OR SSPLv1 in the exact upstream release licence. | Separate components and licences. **Blocking non-AGPL licence choice/compatibility issue** for a claim that all runtime dependencies are AGPL-compatible. Determine distribution/aggregation obligations or select a reviewed alternative (e.g. assess an appropriately licensed server version/implementation with tests). No unilateral replacement or legal conclusion is made. Optional workers do not erase this issue. |
| L6 | PDF.js 6.4.299 declares Apache-2.0, but ships LiberationSans fonts under a GPL v2 agreement with explicit exceptions; Foxit/JBIG2/OpenJPEG/qcms and CMap/ICC texts also differ. | Review font distribution/source obligations and combination/aggregation analysis. **Unresolved potentially incompatible case**; do not label the entire PDF.js payload Apache/OFL. The font exception about documents does not itself clear distributing a font in a web app. |
| L7 | React 19.3.1, React DOM 19.3.1, scheduler 0.28.0: MIT texts inspected. Prior web build listed React but did not emit full MIT notices. | Fixed: emitted `licenses/{react,react-dom,scheduler}.txt`, plus `/LICENSE.txt` for the application; build output checked. Browser asset changes require rebuilt web image. |
| L8 | Bricolage/Atkinson fonts have OFL-1.1 texts; no versioned font manifest. PDF.js's ICC is CC0; CMaps and codecs have their own retained texts. | Exact local asset licence-text hashes preserved; complete binary provenance and any reserved-name/subsetting obligations still need review. No font version invented. |
| L9 | Optional `@napi-rs/canvas` 1.1.0 and 11 platform variants are MIT in the lockfile; not all binaries are installed on this host. | Browser shipping/use differs from npm resolver closure. Audit the exact selected native artifact and embedded third-party code if distributed; do not infer its dependencies' licences from the wrapper field. |
| L10 | Pillow's installed licence contains AOM, Brotli, bzip2, FreeType and other vendored notices; cryptography and psycopg binary wheels may carry native code not in OS package databases. pip bundles further libraries. | Preserve texts; enumerate exact native versions/source and obligations in final-image SBOMs. The application inventory is insufficient to close these items. |
| L11 | OS package inventories include Debian and Alpine licence declarations/texts. Optional OCR includes Tesseract 5.5.0, Leptonica 1.84.1 and Poppler; converter includes LibreOffice 25.2.3.2 and fonts; PostgreSQL is 17.11 with pgvector 0.8.1 pinned/checksummed by Dockerfile. | Each OS package's exact installed version and raw licence evidence is in container-register. Debian labels may be custom expressions or NOASSERTION. Review GPL/LGPL/multiple-licence portions, notices and corresponding source per exact image. Compiled base-image software outside package managers also needs source/licence bundles. No clearance of the images is claimed. |
| L12 | Older planning documents mention PyMuPDF, OCRmyPDF, Dramatiq, python-docx and openpyxl as proposals. They are absent from actual Python runtime pins. | Do not audit them as installed merely because the plan named them. The actual path uses pypdf/PDF.js and optional native services. Native LXC packaging must have its own exact package audit. |

## Current primary web references

Accessed **2026-11-03**, in addition to local artifact inspection on that date:

- [SPDX AGPL-3.0-only text](https://raw.githubusercontent.com/spdx/license-list-data/main/text/AGPL-3.0-only.txt),
  [MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/): identification and obligations review references.
  GNU AGPL/LGPL web fetches timed out during verification; the repository and installed LGPL texts
  were read; no successful fetch is claimed for those pages.
- [Official SPDX 2.3 JSON schema](https://raw.githubusercontent.com/spdx/spdx-spec/v2.3/schemas/spdx-schema.json),
  retained with its hash, and [Docker Scout SBOM reference](https://docs.docker.com/reference/cli/docker/scout/sbom/):
  scanner/schema method. `local://` prevents image resolution from falling back to a registry.
- [Redis 7.4.12 licence](https://raw.githubusercontent.com/redis/redis/7.4.12/LICENSE.txt): exact server release terms.
- [PDF.js 6.4.299 Liberation licence](https://raw.githubusercontent.com/mozilla/pdf.js/v6.4.299/external/standard_fonts/LICENSE_LIBERATION):
  exact bundled font terms, consistent with the inspected installed file.
- [Psycopg installation documentation](https://www.psycopg.org/psycopg3/docs/basic/install.html):
  binary wheel/client-library packaging; current docs are 3.3.7, so the 3.3.6 artifact snapshot is
  the source for this audit's exact version/licence assertion.
- [pgvector 0.8.1 licence](https://raw.githubusercontent.com/pgvector/pgvector/v0.8.1/LICENSE):
  permissive PostgreSQL-style text. M24 adds this exact notice at
  `/usr/local/share/licenses/pgvector/LICENSE` in the rebuilt image; keep it in future source/notice bundles.

## Clearance needed before publication

Resolve L5 and L6, finish native/vendor/source obligations and all NOASSERTIONs (L3/L4/L8–L11),
retain complete notices/corresponding sources for final images, and obtain an accountable licence
review/owner sign-off. Do not check off M24's "all runtime dependencies compatible" criterion yet.
