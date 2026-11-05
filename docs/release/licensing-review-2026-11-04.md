# M24 licensing investigation — 2026-11-04

**Technical investigation; publication remains blocked.** Based on GitHub main
`4688e041bc9d5a0c3226440f0a43b0a899765c44` (PR #32 merged). Work is isolated on
`codex/m24-licensing-policies`; pre-existing README/known-issues changes in the
original checkout were not copied or overwritten. This report updates, rather
than silently replaces, the historical [audit](dependency-audit.md).

## What is distributed

[New inspection index](evidence/licensing-2026-11-04/inspection.json) records full
image IDs, labels, platform and hashes of six offline inspections. The five local
FileCanopy images were built from `816a2fc4e7efb42aa3d59adcb2710244eb675962`;
`git diff 816a2fc origin/main -- apps workers converter ocr deploy` was empty.
This establishes matching committed inputs, not identical future apt/PyPI output
or a fresh main build. Redis is the exact Compose digest. Existing 2026-11-03
SBOMs refer to different image IDs and remain historical; do not attach their
successful validation to these images or the eventual candidate.

| Distribution | Actual payload / boundary |
|---|---|
| API, migrate and optional worker | Same Python runtime image; all 34 pinned dependencies and pip are present even when optional functions are disabled. Psycopg binary, cryptography and Pillow contain native code beyond dpkg's view. |
| nginx web image / browser | Final stage copies only `/web/dist`, not node_modules or the Node build stage. Vite copies all CMaps, standard fonts, ICC and non-QuickJS WASM/fallback assets. PDF.js/React/React DOM/scheduler JS and the three local WOFF2 fonts ship. [Complete inspected browser hashes](evidence/licensing-2026-11-04/web-assets.sha256). |
| Canvas | `@napi-rs/canvas` 1.1.0 and platform variants are optional Node dependencies in the lock closure. No `.node`, `.so`, `.dll` or `.dylib` asset appears in the browser payload; the final image copies no native canvas package. PDF.js JS retains Node import strings; those strings do not establish binary distribution. Native canvas/Skia clearance is unnecessary for this final browser image, but required if a Node renderer, build image, dependency cache or node_modules is distributed. |
| Node/Vite/TypeScript/eslint/test tools | Build or developer environment only; not shipped by the nginx final-stage COPY. Their presence in a lockfile is not runtime-image evidence. A public source repository still carries its own notices. |
| Redis server | Separate optional `workers` service, fetched by digest by operators; not copied into FileCanopy images. `redis` 8.1.0 Python client is a separate MIT component and is always installed. An image mirror/export/bundle would redistribute the server. Optionality is not clearance for that act. |
| Converter/OCR | Separately built/distributable optional images, not merely tools installed on an operator's host. LibreOffice, Poppler, Tesseract, fonts and every base-image binary need their own terms/source treatment when these images ship. |
| PostgreSQL | Always runs as a separate server; pgvector 0.8.1 is built with checksummed source and copied into the final image. Its build tools are confined to the build stage. PostgreSQL itself is compiled outside apk's package list. |
| Native LXC | No native package produced or assessed here. No M25 implementation. |

## Redis server decision (L5)

The pinned server is **7.4.12**, not Redis 8. Its [exact licence](https://github.com/redis/redis/blob/7.4.12/LICENSE.txt)
offers RSALv2 or SSPLv1; Redis 8's later AGPL option cannot be retroactively applied
to this binary. The exact text is retained in
[upstream materials](evidence/licensing-2026-11-04/upstream/sources.json).
The inspected image had **no licence files in `/usr/share`, `/usr/local/share`
or `/usr/local/lib`** under the probe's licence/copyright name rules; that is a
bounded search, not proof of absence everywhere.

RSALv2 permits distribution subject to its restrictions and requires passing on
the agreement; its service/functionality restrictions need specialist assessment
for the intended delivery and operator use. SSPLv1 has source/conveyance conditions
and section 13 service-source requirements. Neither can simply be relabelled AGPL.
FileCanopy talks to a separate server through the standard client protocol; sharing
a Compose file or network does not itself prove a derivative combined work. Resolve
the distribution/service obligations separately from application linking compatibility.
No RSAL/SSPL choice has been made on the owner's behalf.

**Recommendation: approve a separate change to the optional heartbeat server to
Valkey 9.0.6**, with image audit and final integration checks before adoption.
The evaluated Linux amd64 image is
`valkey/valkey@sha256:187679e3bd4036959631e3f03983ab2ba503ab21e6fd0454d508e909db2ee989`.
[Exact 9.0.6 COPYING](https://github.com/valkey-io/valkey/blob/9.0.6/COPYING)
is BSD-3-Clause, with Valkey and Redis notices; bundled dependencies/base packages
still need notice review. A BSD licence for the server does not clear the whole image.

[Probe evidence](evidence/licensing-2026-11-04/valkey-compatibility.json) uses the
unchanged redis-py 8.1.0, actual `workers.heartbeat` and `app.health.check_optional`:
PING, missing-heartbeat status, SET with 30-second expiry, SCAN with prefix/count,
healthy status, SIGTERM, natural expiry, server unavailable and restart recovery
all passed. Disposable internal network, no published port; resources removed.
This tests the implemented command surface, not arbitrary Redis features, security
clearance, production load or another architecture.

Current Compose disables RDB/AOF and mounts no Redis data; this server stores only
ephemeral heartbeat keys. Migration can stop the worker/server, start Valkey empty,
then restart the worker; health recovers on a new heartbeat. Preserve service alias
`redis` and `FILECANOPY_REDIS_URL` to avoid unrelated API/config renaming. Update the
server command/user/health CLI (`valkey-server`, `valkey`, `valkey-cli`) and all
packaging assertions together. Do not copy Redis 7.4 RDB/AOF into Valkey:
[upstream migration guidance](https://valkey.io/topics/migration/) explicitly
does not support those files. Operators with custom persistence/queues require an
explicit export/migration plan. Rollback recreates the old empty heartbeat server;
no PostgreSQL or library data is involved. No dependency replacement is implemented here.

Alternatives: keep 7.4.12 only with a reviewed RSAL/SSPL distribution decision;
evaluate an exact Redis 8 version under its
[AGPL choice](https://github.com/redis/redis/blob/8.2.2/LICENSE.txt), retaining its
corresponding source and native notices; or evaluate an exact BSD Redis 7.2 release
with current maintenance/security evidence. An older BSD version is not automatically
an acceptable security downgrade. Neither alternative was runtime-tested here.

## PDF.js and separately licensed assets (L6/L8/L9)

PDF.js **6.4.299** [identifies its unmodified Liberation Sans fonts as 1.07.4](https://github.com/mozilla/pdf.js/blob/v6.4.299/external/standard_fonts/README.md).
The shipped [licence and exceptions](https://github.com/mozilla/pdf.js/blob/v6.4.299/external/standard_fonts/LICENSE_LIBERATION)
apply to the fonts; the document-embedding exception does not by itself exempt
font redistribution. GPL v2 section 2 treats independent aggregation separately;
section 3 addresses executable/object-code source delivery. The physical-product
exception additionally preserves modification/reinstallation rights for that case.
Do not infer either blanket AGPL incompatibility or blanket exemption from browser delivery.

The upstream historical source and TTF URLs both returned 404. Upstream Git history
identifies [release commit dbeb786b](https://github.com/liberationfonts/liberation-1.7-fonts/commit/dbeb786b4ede1eaec2ec5cb8d3b75d21641e2a35)
as the 1.07.4 release; its Makefile declares 1.07.4. Its complete unmodified archive
is now retained and shipped at `/sources/liberation-1.07.4-dbeb786b.tar.gz`, together
with provenance/build guidance. The archive contains editable SFD files, FontForge
scripts, Makefile and both licence files. This materially addresses source availability
using equivalent access alongside the font binaries, rather than promising a
three-year written offer without an owner able to fulfil it. Font binaries are
unchanged. Source correspondence relies on upstream's exact-version identification;
a font rebuild and specialist confirmation of this delivery/aggregation treatment
remain open. Replacing with Liberation 2.x is not recommended in this task: glyph
order and scale-factor tables must be regenerated and rendering tested first.

| Shipped PDF.js asset | Retained terms / disposition |
|---|---|
| PDF.js library/worker | Apache-2.0; `/pdfjs/LICENSE` retained. |
| Foxit PFB fonts | PDFium/Foxit BSD-style terms; `/pdfjs/standard_fonts/LICENSE_FOXIT`. |
| Adobe CMaps | Separate Adobe redistribution text in `cmaps/LICENSE`, retained verbatim; not merely assumed Apache. |
| OpenJPEG and PDF.js port | BSD-2-Clause texts `LICENSE_OPENJPEG`, `LICENSE_PDFJS_OPENJPEG`. |
| JBIG2 and PDF.js port | PDFium BSD-style and port notice `LICENSE_JBIG2`, `LICENSE_PDFJS_JBIG2`; WASM and fallback JS retained. |
| qcms and PDF.js port | Separate MIT-style qcms text and port notice `LICENSE_QCMS`, `LICENSE_PDFJS_QCMS`. |
| ICC | CC0-1.0 `/pdfjs/iccs/LICENSE`. |
| QuickJS | WASM/JS excluded by build filter; scripting disabled. Strings in the worker are not a shipped QuickJS executable. |
| Bricolage/Atkinson WOFF2 | OFL-1.1 texts retained. Hashes identify the exact repository files. Original upstream binary version and subset/rename provenance remain unresolved; do not replace their identity with a guessed Fontsource version. |

## Native/vendor findings (L2/L3/L4/L10/L11)

[API native probe](evidence/licensing-2026-11-04/api-native.json) records hashes of
vendored `.so` files and versions reported by loaded libraries. It is more specific
than wheel metadata, but does not identify every statically compiled dependency.

| Component | Verified evidence | Disposition before publication |
|---|---|---|
| psycopg / psycopg-binary 3.3.6 | LGPL-3.0-only installed texts; libpq reports **18.6** (`180006`), vendored SSL reports **3.5.8**. Hashes/names identify additional Kerberos, LDAP/lber, SASL, keyutils, crypt, SELinux, PCRE/com_err libraries. | No automatic application incompatibility. Added explicit LGPL usage/reverse-engineering notice, GPL v3 text, exact libpq/OpenSSL notices. Retain wheel build recipe, library sources/patches and a demonstrated replacement/relink route satisfying LGPL section 4. PyPI lists no psycopg-binary sdist; psycopg's wrapper sdist is insufficient. Other native versions/licences remain to reconcile. |
| cryptography 50.0.2 | Apache-2.0 OR BSD-3-Clause; embedded OpenSSL reports **4.0.3**, separate from wheel libpq and OS SSL. | Added exact OpenSSL Apache text. Retrieve exact Rust crate closure/notices and native build sources/recipe; a top-level dual licence is not the native inventory. |
| Pillow 13.0.0 | Installed multi-component licence retained. Loaded features: FreeType 2.14.3, LittleCMS 2.19, libwebp 1.6.0, libavif 1.4.2, OpenJPEG 2.5.4, libtiff 4.7.2, libjpeg-turbo 3.2.0, zlib-ng 2.3.3. ABI/version strings such as JPEG 6.2 are not upstream libjpeg-turbo release identifiers. | Retain all bundled notices (AOM/Brotli/bzip2/FreeType/etc.) and map remaining static libraries/codecs and exact source/patches. File hashes/ABI names alone do not resolve every version. Select documented FreeType licence alternative and preserve required acknowledgement if relying on FTL. |
| certifi 2026.10.23 | MPL-2.0; PyPI sdist SHA-256 verified; exact source archive added under API `vendor-sources`. | Main certifi source-delivery mechanism prepared and byte comparison recorded by verification. Keep MPL notice/source archive in final image. Pip's separate certifi **2026.10.19** is supplied in the exact pip source below. This does not relicense the application or close all L10. |
| pip 26.2.2 / base Python | pip/vendor licence files retained; exact pip sdist hash-verified, packaged in API vendor-sources and installed vendor files byte-compared. Includes modified certifi 2026.10.19 and vendor manifest/licences/build materials. Base Python built outside dpkg. | Pip source delivery materially advanced. Review final vendor notices/source packaging; retain CPython 3.13.18 exact source/build configuration and base-image recipe separately. Do not assume `requirements.txt` inventories these components. |
| Converter | LibreOffice binary package `4:25.2.3-2+deb13u8`; source `libreoffice` same version; Liberation2 transitional package and fonts-liberation **1:2.1.5-3**. | These OFL font packages are distinct from PDF.js GPL 1.07.4 fonts. Reconcile LibreOffice's per-file MPL/LGPL and embedded code; retain Debian source/patches/build scripts. Unix-socket invocation is separate-process use, not evidence the API links LibreOffice. |
| OCR | Tesseract `5.5.0-1+b1`, source `5.5.0-1`; Leptonica `1.84.1-4`; Poppler `25.03.0-5+deb13u4`; trained data `1:4.1.0-2`. | Tesseract Apache/MIT and Leptonica BSD-style texts retained. Poppler GPL source obligations apply to that distributed executable/library; its presence in a separate OCR image does not force every app package under GPL. Retain patched Debian sources and trained-data notices. |
| Debian/Alpine and compiled bases | Full dpkg binary→source versions or raw apk database retained for all six images, plus actual licence files/common-licenses. nginx/PostgreSQL/Redis/CPython compiled outside package manager need separate mapping. | Exact Debian source version may differ from binary (`+b1`) and must include Debian patches. Alpine `o:` origin plus installed version locates the build recipe; preserve its source checksums/patches. Base image digest is not a source archive or source offer. |

## NOASSERTION treatment

The historical scanner output is unmodified. A missing scanner licence is an
evidence gap, not a legal incompatibility. Conversely, a nonempty scanner field
does not fulfil source or notice obligations. The following manual metadata
findings resolve three missing identifications in the container register and
confirm Expat's already-declared MIT terms for
the stated versions, using retained **installed copyright text**, including
dereferenced symlinks. They do not clear every file in the source package.

| Package/version | Manual identification / primary artifact |
|---|---|
| libexpat1 2.9.0-1~deb13u1 | MIT, both upstream and Debian sections; `ocr-notices.tar.gz:usr/share/doc/libexpat1/copyright`. Confirmation of existing metadata, not a newly resolved unknown. |
| libpixman-1-0 0.44.0-3 | MIT-style permission/disclaimer (including its following paragraph), full copyright list retained; `ocr-notices.tar.gz:usr/share/doc/libpixman-1-0/copyright`. |
| libxau6 1:1.0.11-1 | X11-style permission with advertising/name-use restriction retained; `ocr-notices.tar.gz:usr/share/doc/libxau6/copyright`. Do not flatten it to an MIT label and discard text. |
| libncursesw6 6.5+20250216-2 | Upstream MIT/X11, install-sh X11, listed termcap/tset files BSD-3-clause; `ocr-notices.tar.gz:usr/share/doc/libncursesw6/copyright` resolves the documentation symlink. |

Other custom expressions and NOASSERTIONs remain pending per-package review,
especially compiler runtime exceptions, libcrypt/keyutils/SELinux, libc, Python,
documentation, binary data and Alpine packages with stripped licence payloads.
The [source delivery checklist](source-delivery.md) is the remaining work queue.
No image-wide "all unknowns resolved" claim is made.

[Historical scanner review queue](evidence/licensing-2026-11-04/noassertion-review.json)
preserves all **244** NOASSERTION package identities. Eight entries (pixman/Xau
binary/source identities across converter and OCR) now have a hashed manual text
identification; **236 remain pending**. These counts concern licence identification,
not source/notice fulfilment. Ncurses was missing in the container register while
the scanner already declared terms; Expat was declared in both. No scanner fields
or historical validation documents were changed.

## Finding-by-finding status

| Original ID | Current disposition |
|---|---|
| L1 | Application declaration consistent; third-party terms remain separate. |
| L2 | Top-level texts retained; no new automatic incompatibility found. Native closure still L10. |
| L3 | Advanced: loaded native versions/hashes, source gap and LGPL replacement requirements identified; notices added. Source/relink evidence blocks clearance. |
| L4 | Main certifi exact source retained and packaged; pip's separate certifi source also supplied via exact pip sdist. Verify preservation/extraction/identity on final candidate. |
| L5 | Advanced: choices analysed; separate Valkey recommendation with actual compatibility evidence. Owner must select change or specialist review of retained server terms. No server change made. |
| L6 | Advanced: exact font version/release source recovered and shipped beside fonts, all notices retained. Specialist aggregation/source-correspondence review remains. No unsupported blanket incompatibility claim. |
| L7 | Existing MIT/AGPL notice repair retained and rebuilt/verified. |
| L8 | PDF auxiliary asset notices identified; local WOFF2 provenance/modified-name conditions remain. |
| L9 | Resolved for the inspected final runtime/browser: no distributed canvas native binary. Reopen if shipping builder/node_modules or Node rendering. |
| L10 | Advanced: exact native evidence and partial notice/source assembly; Rust/static/vendor versions and source/rebuild completeness remain. |
| L11 | Advanced: all six installed package/source mappings and notice bundles retained; three missing register identifications resolved, Expat confirmed, eight historical scanner entries manually identified and 236 pending. Exact patched source delivery and remaining unknowns block publication. |
| L12 | Planning-only PyMuPDF/OCRmyPDF/Dramatiq/docx/openpyxl remain absent from runtime pins. |

Before publication obtain an accountable licensing review of font combination and
source correspondence, LGPL replacement/source sufficiency, and whichever Redis
decision is selected. Finish the remaining vendor/OS notices and source delivery,
regenerate final-image inventories/SBOMs and verify published source access. No
specialist legal sign-off has been supplied by this investigation.
