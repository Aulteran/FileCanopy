# Dependency register — artifact declarations, not compatibility clearance

Current scope corrections and native evidence: [2026-11-04 review](licensing-review-2026-11-04.md).
In particular, optional canvas lock entries below are Node/build closure, not
native binaries distributed by the final browser/nginx image. The historical
application inventory is not a statement that every listed package ships to a browser.

Inspected 2026-11-03. Python image: `sha256:44299f70c8b88b2a4894d57a024051d3935feac6a76ec37db4c24a15782d12c5` (linux/amd64).

Every Python requirement matched the installed version. Sources below identify upstream versions; the evidence actually read is installed METADATA/licence text and local lockfiles, retained in evidence/. Registry pages were not individually fetched. Build/test dependencies are separately labelled. Optional npm platform packages are included even when absent on this host. Native bundled and OS components require the separate audit review.

## Python

| Dependency | Version | Declared licence | Scope | Source / evidence |
|---|---|---|---|---|
| alembic | 1.20.1 | MIT | runtime requirement | [upstream version](https://pypi.org/project/alembic/1.20.1/); [METADATA and licence texts](evidence/python-runtime.json) |
| annotated-doc | 0.0.5 | MIT | runtime requirement | [upstream version](https://pypi.org/project/annotated-doc/0.0.5/); [METADATA and licence texts](evidence/python-runtime.json) |
| annotated-types | 0.8.0 | MIT | runtime requirement | [upstream version](https://pypi.org/project/annotated-types/0.8.0/); [METADATA and licence texts](evidence/python-runtime.json) |
| anyio | 4.15.1 | MIT | runtime requirement | [upstream version](https://pypi.org/project/anyio/4.15.1/); [METADATA and licence texts](evidence/python-runtime.json) |
| argon2-cffi | 25.1.0 | MIT | runtime requirement | [upstream version](https://pypi.org/project/argon2-cffi/25.1.0/); [METADATA and licence texts](evidence/python-runtime.json) |
| argon2-cffi-bindings | 25.1.0 | MIT | runtime requirement | [upstream version](https://pypi.org/project/argon2-cffi-bindings/25.1.0/); [METADATA and licence texts](evidence/python-runtime.json) |
| certifi | 2026.10.23 | MPL-2.0 | runtime requirement | [upstream version](https://pypi.org/project/certifi/2026.10.23/); [METADATA and licence texts](evidence/python-runtime.json) |
| cffi | 2.1.1 | MIT-0 | runtime requirement | [upstream version](https://pypi.org/project/cffi/2.1.1/); [METADATA and licence texts](evidence/python-runtime.json) |
| click | 8.5.0 | BSD-3-Clause | runtime requirement | [upstream version](https://pypi.org/project/click/8.5.0/); [METADATA and licence texts](evidence/python-runtime.json) |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | runtime requirement | [upstream version](https://pypi.org/project/cryptography/50.0.2/); [METADATA and licence texts](evidence/python-runtime.json) |
| fastapi | 0.162.0 | MIT | runtime requirement | [upstream version](https://pypi.org/project/fastapi/0.162.0/); [METADATA and licence texts](evidence/python-runtime.json) |
| h11 | 0.16.0 | MIT | runtime requirement | [upstream version](https://pypi.org/project/h11/0.16.0/); [METADATA and licence texts](evidence/python-runtime.json) |
| httpcore | 1.0.9 | BSD-3-Clause | runtime requirement | [upstream version](https://pypi.org/project/httpcore/1.0.9/); [METADATA and licence texts](evidence/python-runtime.json) |
| httpx | 0.28.1 | BSD-3-Clause | runtime requirement | [upstream version](https://pypi.org/project/httpx/0.28.1/); [METADATA and licence texts](evidence/python-runtime.json) |
| idna | 3.20 | BSD-3-Clause | runtime requirement | [upstream version](https://pypi.org/project/idna/3.20/); [METADATA and licence texts](evidence/python-runtime.json) |
| Mako | 1.4.3 | MIT | runtime requirement | [upstream version](https://pypi.org/project/Mako/1.4.3/); [METADATA and licence texts](evidence/python-runtime.json) |
| MarkupSafe | 3.0.4 | BSD-3-Clause | runtime requirement | [upstream version](https://pypi.org/project/MarkupSafe/3.0.4/); [METADATA and licence texts](evidence/python-runtime.json) |
| opentelemetry-api | 1.45.2 | Apache-2.0 | runtime requirement | [upstream version](https://pypi.org/project/opentelemetry-api/1.45.2/); [METADATA and licence texts](evidence/python-runtime.json) |
| pillow | 13.0.0 | MIT-CMU | runtime requirement | [upstream version](https://pypi.org/project/pillow/13.0.0/); [METADATA and licence texts](evidence/python-runtime.json) |
| pip | 26.2.2 | MIT | image tooling / unpinned extra | [upstream version](https://pypi.org/project/pip/26.2.2/); [METADATA and licence texts](evidence/python-runtime.json) |
| psycopg | 3.3.6 | LGPL-3.0-only | runtime requirement | [upstream version](https://pypi.org/project/psycopg/3.3.6/); [METADATA and licence texts](evidence/python-runtime.json) |
| psycopg-binary | 3.3.6 | LGPL-3.0-only | runtime requirement | [upstream version](https://pypi.org/project/psycopg-binary/3.3.6/); [METADATA and licence texts](evidence/python-runtime.json) |
| pycparser | 3.12 | BSD-3-Clause | runtime requirement | [upstream version](https://pypi.org/project/pycparser/3.12/); [METADATA and licence texts](evidence/python-runtime.json) |
| pydantic | 2.14.2 | MIT | runtime requirement | [upstream version](https://pypi.org/project/pydantic/2.14.2/); [METADATA and licence texts](evidence/python-runtime.json) |
| pydantic-settings | 2.16.2 | MIT | runtime requirement | [upstream version](https://pypi.org/project/pydantic-settings/2.16.2/); [METADATA and licence texts](evidence/python-runtime.json) |
| pydantic_core | 2.50.2 | MIT | runtime requirement | [upstream version](https://pypi.org/project/pydantic_core/2.50.2/); [METADATA and licence texts](evidence/python-runtime.json) |
| PyJWT | 2.10.1 | MIT | runtime requirement | [upstream version](https://pypi.org/project/PyJWT/2.10.1/); [METADATA and licence texts](evidence/python-runtime.json) |
| pypdf | 6.21.4 | BSD-3-Clause | runtime requirement | [upstream version](https://pypi.org/project/pypdf/6.21.4/); [METADATA and licence texts](evidence/python-runtime.json) |
| python-dotenv | 1.2.4 | BSD-3-Clause | runtime requirement | [upstream version](https://pypi.org/project/python-dotenv/1.2.4/); [METADATA and licence texts](evidence/python-runtime.json) |
| redis | 8.1.0 | MIT | runtime requirement | [upstream version](https://pypi.org/project/redis/8.1.0/); [METADATA and licence texts](evidence/python-runtime.json) |
| SQLAlchemy | 2.1.6 | MIT | runtime requirement | [upstream version](https://pypi.org/project/SQLAlchemy/2.1.6/); [METADATA and licence texts](evidence/python-runtime.json) |
| starlette | 1.10.0 | BSD-3-Clause | runtime requirement | [upstream version](https://pypi.org/project/starlette/1.10.0/); [METADATA and licence texts](evidence/python-runtime.json) |
| typing-inspection | 0.4.4 | MIT | runtime requirement | [upstream version](https://pypi.org/project/typing-inspection/0.4.4/); [METADATA and licence texts](evidence/python-runtime.json) |
| typing_extensions | 4.16.0 | PSF-2.0 | runtime requirement | [upstream version](https://pypi.org/project/typing_extensions/4.16.0/); [METADATA and licence texts](evidence/python-runtime.json) |
| uvicorn | 0.54.0 | BSD-3-Clause | runtime requirement | [upstream version](https://pypi.org/project/uvicorn/0.54.0/); [METADATA and licence texts](evidence/python-runtime.json) |

## npm runtime closure

| Dependency | Version | Declared licence | Optional | Source / evidence |
|---|---|---|---|---|
| @napi-rs/canvas | 1.1.0 | MIT | true | [artifact](https://registry.npmjs.org/@napi-rs/canvas/-/canvas-1.1.0.tgz); apps/web/package-lock.json#/packages/node_modules~1@napi-rs~1canvas |
| @napi-rs/canvas-android-arm64 | 1.1.0 | MIT | true | [artifact](https://registry.npmjs.org/@napi-rs/canvas-android-arm64/-/canvas-android-arm64-1.1.0.tgz); apps/web/package-lock.json#/packages/node_modules~1@napi-rs~1canvas-android-arm64 |
| @napi-rs/canvas-darwin-arm64 | 1.1.0 | MIT | true | [artifact](https://registry.npmjs.org/@napi-rs/canvas-darwin-arm64/-/canvas-darwin-arm64-1.1.0.tgz); apps/web/package-lock.json#/packages/node_modules~1@napi-rs~1canvas-darwin-arm64 |
| @napi-rs/canvas-darwin-x64 | 1.1.0 | MIT | true | [artifact](https://registry.npmjs.org/@napi-rs/canvas-darwin-x64/-/canvas-darwin-x64-1.1.0.tgz); apps/web/package-lock.json#/packages/node_modules~1@napi-rs~1canvas-darwin-x64 |
| @napi-rs/canvas-linux-arm-gnueabihf | 1.1.0 | MIT | true | [artifact](https://registry.npmjs.org/@napi-rs/canvas-linux-arm-gnueabihf/-/canvas-linux-arm-gnueabihf-1.1.0.tgz); apps/web/package-lock.json#/packages/node_modules~1@napi-rs~1canvas-linux-arm-gnueabihf |
| @napi-rs/canvas-linux-arm64-gnu | 1.1.0 | MIT | true | [artifact](https://registry.npmjs.org/@napi-rs/canvas-linux-arm64-gnu/-/canvas-linux-arm64-gnu-1.1.0.tgz); apps/web/package-lock.json#/packages/node_modules~1@napi-rs~1canvas-linux-arm64-gnu |
| @napi-rs/canvas-linux-arm64-musl | 1.1.0 | MIT | true | [artifact](https://registry.npmjs.org/@napi-rs/canvas-linux-arm64-musl/-/canvas-linux-arm64-musl-1.1.0.tgz); apps/web/package-lock.json#/packages/node_modules~1@napi-rs~1canvas-linux-arm64-musl |
| @napi-rs/canvas-linux-riscv64-gnu | 1.1.0 | MIT | true | [artifact](https://registry.npmjs.org/@napi-rs/canvas-linux-riscv64-gnu/-/canvas-linux-riscv64-gnu-1.1.0.tgz); apps/web/package-lock.json#/packages/node_modules~1@napi-rs~1canvas-linux-riscv64-gnu |
| @napi-rs/canvas-linux-x64-gnu | 1.1.0 | MIT | true | [artifact](https://registry.npmjs.org/@napi-rs/canvas-linux-x64-gnu/-/canvas-linux-x64-gnu-1.1.0.tgz); apps/web/package-lock.json#/packages/node_modules~1@napi-rs~1canvas-linux-x64-gnu |
| @napi-rs/canvas-linux-x64-musl | 1.1.0 | MIT | true | [artifact](https://registry.npmjs.org/@napi-rs/canvas-linux-x64-musl/-/canvas-linux-x64-musl-1.1.0.tgz); apps/web/package-lock.json#/packages/node_modules~1@napi-rs~1canvas-linux-x64-musl |
| @napi-rs/canvas-win32-arm64-msvc | 1.1.0 | MIT | true | [artifact](https://registry.npmjs.org/@napi-rs/canvas-win32-arm64-msvc/-/canvas-win32-arm64-msvc-1.1.0.tgz); apps/web/package-lock.json#/packages/node_modules~1@napi-rs~1canvas-win32-arm64-msvc |
| @napi-rs/canvas-win32-x64-msvc | 1.1.0 | MIT | true | [artifact](https://registry.npmjs.org/@napi-rs/canvas-win32-x64-msvc/-/canvas-win32-x64-msvc-1.1.0.tgz); apps/web/package-lock.json#/packages/node_modules~1@napi-rs~1canvas-win32-x64-msvc |
| pdfjs-dist | 6.4.299 | Apache-2.0 | false | [artifact](https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-6.4.299.tgz); apps/web/package-lock.json#/packages/node_modules~1pdfjs-dist |
| react | 19.3.1 | MIT | false | [artifact](https://registry.npmjs.org/react/-/react-19.3.1.tgz); apps/web/package-lock.json#/packages/node_modules~1react |
| react-dom | 19.3.1 | MIT | false | [artifact](https://registry.npmjs.org/react-dom/-/react-dom-19.3.1.tgz); apps/web/package-lock.json#/packages/node_modules~1react-dom |
| scheduler | 0.28.0 | MIT | false | [artifact](https://registry.npmjs.org/scheduler/-/scheduler-0.28.0.tgz); apps/web/package-lock.json#/packages/node_modules~1scheduler |

## npm build and test (not application runtime)

| Dependency | Version | Declared licence | Manifest | Source |
|---|---|---|---|---|
| @cacheable/memory | 2.2.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/@cacheable/memory/-/memory-2.2.0.tgz) |
| @cacheable/utils | 2.5.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/@cacheable/utils/-/utils-2.5.0.tgz) |
| @eslint-community/eslint-utils | 4.10.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/@eslint-community/eslint-utils/-/eslint-utils-4.10.1.tgz) |
| eslint-visitor-keys | 3.4.3 | Apache-2.0 | package-lock.json | [artifact](https://registry.npmjs.org/eslint-visitor-keys/-/eslint-visitor-keys-3.4.3.tgz) |
| @eslint-community/regexpp | 4.12.2 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/@eslint-community/regexpp/-/regexpp-4.12.2.tgz) |
| @eslint/config-array | 0.23.5 | Apache-2.0 | package-lock.json | [artifact](https://registry.npmjs.org/@eslint/config-array/-/config-array-0.23.5.tgz) |
| @eslint/config-helpers | 0.7.0 | Apache-2.0 | package-lock.json | [artifact](https://registry.npmjs.org/@eslint/config-helpers/-/config-helpers-0.7.0.tgz) |
| @eslint/core | 1.2.1 | Apache-2.0 | package-lock.json | [artifact](https://registry.npmjs.org/@eslint/core/-/core-1.2.1.tgz) |
| @eslint/js | 10.0.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/@eslint/js/-/js-10.0.1.tgz) |
| @eslint/object-schema | 3.0.5 | Apache-2.0 | package-lock.json | [artifact](https://registry.npmjs.org/@eslint/object-schema/-/object-schema-3.0.5.tgz) |
| @eslint/plugin-kit | 0.7.3 | Apache-2.0 | package-lock.json | [artifact](https://registry.npmjs.org/@eslint/plugin-kit/-/plugin-kit-0.7.3.tgz) |
| @humanfs/core | 0.19.2 | Apache-2.0 | package-lock.json | [artifact](https://registry.npmjs.org/@humanfs/core/-/core-0.19.2.tgz) |
| @humanfs/node | 0.16.8 | Apache-2.0 | package-lock.json | [artifact](https://registry.npmjs.org/@humanfs/node/-/node-0.16.8.tgz) |
| @humanfs/types | 0.15.0 | Apache-2.0 | package-lock.json | [artifact](https://registry.npmjs.org/@humanfs/types/-/types-0.15.0.tgz) |
| @humanwhocodes/module-importer | 1.0.1 | Apache-2.0 | package-lock.json | [artifact](https://registry.npmjs.org/@humanwhocodes/module-importer/-/module-importer-1.0.1.tgz) |
| @humanwhocodes/retry | 0.4.3 | Apache-2.0 | package-lock.json | [artifact](https://registry.npmjs.org/@humanwhocodes/retry/-/retry-0.4.3.tgz) |
| @keyv/bigmap | 1.3.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/@keyv/bigmap/-/bigmap-1.3.1.tgz) |
| @keyv/serialize | 1.1.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/@keyv/serialize/-/serialize-1.1.1.tgz) |
| @types/esrecurse | 4.3.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/@types/esrecurse/-/esrecurse-4.3.1.tgz) |
| @types/estree | 1.0.10 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/@types/estree/-/estree-1.0.10.tgz) |
| @types/json-schema | 7.0.15 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/@types/json-schema/-/json-schema-7.0.15.tgz) |
| acorn | 8.19.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/acorn/-/acorn-8.19.0.tgz) |
| acorn-jsx | 5.3.2 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/acorn-jsx/-/acorn-jsx-5.3.2.tgz) |
| ajv | 6.15.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/ajv/-/ajv-6.15.0.tgz) |
| balanced-match | 4.0.4 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/balanced-match/-/balanced-match-4.0.4.tgz) |
| brace-expansion | 5.0.12 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/brace-expansion/-/brace-expansion-5.0.12.tgz) |
| cacheable | 2.5.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/cacheable/-/cacheable-2.5.0.tgz) |
| cross-spawn | 7.0.6 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/cross-spawn/-/cross-spawn-7.0.6.tgz) |
| debug | 4.4.3 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/debug/-/debug-4.4.3.tgz) |
| deep-is | 0.1.4 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/deep-is/-/deep-is-0.1.4.tgz) |
| escape-string-regexp | 4.0.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/escape-string-regexp/-/escape-string-regexp-4.0.0.tgz) |
| eslint | 10.14.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/eslint/-/eslint-10.14.0.tgz) |
| eslint-scope | 9.1.2 | BSD-2-Clause | package-lock.json | [artifact](https://registry.npmjs.org/eslint-scope/-/eslint-scope-9.1.2.tgz) |
| eslint-visitor-keys | 5.0.1 | Apache-2.0 | package-lock.json | [artifact](https://registry.npmjs.org/eslint-visitor-keys/-/eslint-visitor-keys-5.0.1.tgz) |
| espree | 11.2.0 | BSD-2-Clause | package-lock.json | [artifact](https://registry.npmjs.org/espree/-/espree-11.2.0.tgz) |
| esquery | 1.7.0 | BSD-3-Clause | package-lock.json | [artifact](https://registry.npmjs.org/esquery/-/esquery-1.7.0.tgz) |
| esrecurse | 4.3.0 | BSD-2-Clause | package-lock.json | [artifact](https://registry.npmjs.org/esrecurse/-/esrecurse-4.3.0.tgz) |
| estraverse | 5.3.0 | BSD-2-Clause | package-lock.json | [artifact](https://registry.npmjs.org/estraverse/-/estraverse-5.3.0.tgz) |
| esutils | 2.0.3 | BSD-2-Clause | package-lock.json | [artifact](https://registry.npmjs.org/esutils/-/esutils-2.0.3.tgz) |
| fast-deep-equal | 3.1.3 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/fast-deep-equal/-/fast-deep-equal-3.1.3.tgz) |
| fast-json-stable-stringify | 2.1.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/fast-json-stable-stringify/-/fast-json-stable-stringify-2.1.0.tgz) |
| fast-levenshtein | 2.0.6 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/fast-levenshtein/-/fast-levenshtein-2.0.6.tgz) |
| file-entry-cache | 11.1.5 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/file-entry-cache/-/file-entry-cache-11.1.5.tgz) |
| find-up | 5.0.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/find-up/-/find-up-5.0.0.tgz) |
| flat-cache | 6.1.23 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/flat-cache/-/flat-cache-6.1.23.tgz) |
| flatted | 3.4.4 | ISC | package-lock.json | [artifact](https://registry.npmjs.org/flatted/-/flatted-3.4.4.tgz) |
| glob-parent | 6.0.2 | ISC | package-lock.json | [artifact](https://registry.npmjs.org/glob-parent/-/glob-parent-6.0.2.tgz) |
| globals | 17.13.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/globals/-/globals-17.13.0.tgz) |
| hashery | 1.5.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/hashery/-/hashery-1.5.1.tgz) |
| hookified | 1.15.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/hookified/-/hookified-1.15.1.tgz) |
| ignore | 5.3.2 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/ignore/-/ignore-5.3.2.tgz) |
| imurmurhash | 0.1.4 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/imurmurhash/-/imurmurhash-0.1.4.tgz) |
| is-extglob | 2.1.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/is-extglob/-/is-extglob-2.1.1.tgz) |
| is-glob | 4.0.3 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/is-glob/-/is-glob-4.0.3.tgz) |
| isexe | 2.0.0 | ISC | package-lock.json | [artifact](https://registry.npmjs.org/isexe/-/isexe-2.0.0.tgz) |
| json-schema-traverse | 0.4.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/json-schema-traverse/-/json-schema-traverse-0.4.1.tgz) |
| json-stable-stringify-without-jsonify | 1.0.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/json-stable-stringify-without-jsonify/-/json-stable-stringify-without-jsonify-1.0.1.tgz) |
| keyv | 5.6.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/keyv/-/keyv-5.6.0.tgz) |
| levn | 0.4.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/levn/-/levn-0.4.1.tgz) |
| locate-path | 6.0.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/locate-path/-/locate-path-6.0.0.tgz) |
| minimatch | 10.2.6 | BlueOak-1.0.0 | package-lock.json | [artifact](https://registry.npmjs.org/minimatch/-/minimatch-10.2.6.tgz) |
| ms | 2.1.3 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/ms/-/ms-2.1.3.tgz) |
| natural-compare | 1.4.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/natural-compare/-/natural-compare-1.4.0.tgz) |
| optionator | 0.9.4 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/optionator/-/optionator-0.9.4.tgz) |
| p-limit | 3.1.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/p-limit/-/p-limit-3.1.0.tgz) |
| p-locate | 5.0.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/p-locate/-/p-locate-5.0.0.tgz) |
| path-exists | 4.0.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/path-exists/-/path-exists-4.0.0.tgz) |
| path-key | 3.1.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/path-key/-/path-key-3.1.1.tgz) |
| prelude-ls | 1.2.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/prelude-ls/-/prelude-ls-1.2.1.tgz) |
| prettier | 3.9.10 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/prettier/-/prettier-3.9.10.tgz) |
| punycode | 2.3.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/punycode/-/punycode-2.3.1.tgz) |
| qified | 0.10.1 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/qified/-/qified-0.10.1.tgz) |
| hookified | 2.2.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/hookified/-/hookified-2.2.0.tgz) |
| shebang-command | 2.0.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/shebang-command/-/shebang-command-2.0.0.tgz) |
| shebang-regex | 3.0.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/shebang-regex/-/shebang-regex-3.0.0.tgz) |
| type-check | 0.4.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/type-check/-/type-check-0.4.0.tgz) |
| uri-js | 4.4.1 | BSD-2-Clause | package-lock.json | [artifact](https://registry.npmjs.org/uri-js/-/uri-js-4.4.1.tgz) |
| which | 2.0.2 | ISC | package-lock.json | [artifact](https://registry.npmjs.org/which/-/which-2.0.2.tgz) |
| word-wrap | 1.2.5 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/word-wrap/-/word-wrap-1.2.5.tgz) |
| yocto-queue | 0.1.0 | MIT | package-lock.json | [artifact](https://registry.npmjs.org/yocto-queue/-/yocto-queue-0.1.0.tgz) |
| @jridgewell/resolve-uri | 3.1.2 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@jridgewell/resolve-uri/-/resolve-uri-3.1.2.tgz) |
| @jridgewell/sourcemap-codec | 1.6.0 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@jridgewell/sourcemap-codec/-/sourcemap-codec-1.6.0.tgz) |
| @jridgewell/trace-mapping | 0.3.31 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@jridgewell/trace-mapping/-/trace-mapping-0.3.31.tgz) |
| @oxc-project/types | 0.156.0 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@oxc-project/types/-/types-0.156.0.tgz) |
| @rolldown/binding-android-arm-eabi | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-android-arm-eabi/-/binding-android-arm-eabi-1.2.16.tgz) |
| @rolldown/binding-android-arm64 | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-android-arm64/-/binding-android-arm64-1.2.16.tgz) |
| @rolldown/binding-darwin-arm64 | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-darwin-arm64/-/binding-darwin-arm64-1.2.16.tgz) |
| @rolldown/binding-darwin-x64 | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-darwin-x64/-/binding-darwin-x64-1.2.16.tgz) |
| @rolldown/binding-freebsd-x64 | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-freebsd-x64/-/binding-freebsd-x64-1.2.16.tgz) |
| @rolldown/binding-linux-arm-gnueabihf | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-linux-arm-gnueabihf/-/binding-linux-arm-gnueabihf-1.2.16.tgz) |
| @rolldown/binding-linux-arm64-gnu | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-linux-arm64-gnu/-/binding-linux-arm64-gnu-1.2.16.tgz) |
| @rolldown/binding-linux-arm64-musl | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-linux-arm64-musl/-/binding-linux-arm64-musl-1.2.16.tgz) |
| @rolldown/binding-linux-ppc64-gnu | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-linux-ppc64-gnu/-/binding-linux-ppc64-gnu-1.2.16.tgz) |
| @rolldown/binding-linux-s390x-gnu | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-linux-s390x-gnu/-/binding-linux-s390x-gnu-1.2.16.tgz) |
| @rolldown/binding-linux-x64-gnu | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-linux-x64-gnu/-/binding-linux-x64-gnu-1.2.16.tgz) |
| @rolldown/binding-linux-x64-musl | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-linux-x64-musl/-/binding-linux-x64-musl-1.2.16.tgz) |
| @rolldown/binding-openharmony-arm64 | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-openharmony-arm64/-/binding-openharmony-arm64-1.2.16.tgz) |
| @rolldown/binding-win32-arm64-msvc | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-win32-arm64-msvc/-/binding-win32-arm64-msvc-1.2.16.tgz) |
| @rolldown/binding-win32-x64-msvc | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/binding-win32-x64-msvc/-/binding-win32-x64-msvc-1.2.16.tgz) |
| @rolldown/pluginutils | 1.0.1 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@rolldown/pluginutils/-/pluginutils-1.0.1.tgz) |
| @types/chai | 5.2.3 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@types/chai/-/chai-5.2.3.tgz) |
| @types/deep-eql | 4.0.2 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@types/deep-eql/-/deep-eql-4.0.2.tgz) |
| @types/estree | 1.0.10 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@types/estree/-/estree-1.0.10.tgz) |
| @types/node | 22.20.5 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@types/node/-/node-22.20.5.tgz) |
| @types/react | 19.3.0 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@types/react/-/react-19.3.0.tgz) |
| @types/react-dom | 19.3.0 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@types/react-dom/-/react-dom-19.3.0.tgz) |
| @typescript/typescript-aix-ppc64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-aix-ppc64/-/typescript-aix-ppc64-7.0.2.tgz) |
| @typescript/typescript-darwin-arm64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-darwin-arm64/-/typescript-darwin-arm64-7.0.2.tgz) |
| @typescript/typescript-darwin-x64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-darwin-x64/-/typescript-darwin-x64-7.0.2.tgz) |
| @typescript/typescript-freebsd-arm64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-freebsd-arm64/-/typescript-freebsd-arm64-7.0.2.tgz) |
| @typescript/typescript-freebsd-x64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-freebsd-x64/-/typescript-freebsd-x64-7.0.2.tgz) |
| @typescript/typescript-linux-arm | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-linux-arm/-/typescript-linux-arm-7.0.2.tgz) |
| @typescript/typescript-linux-arm64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-linux-arm64/-/typescript-linux-arm64-7.0.2.tgz) |
| @typescript/typescript-linux-loong64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-linux-loong64/-/typescript-linux-loong64-7.0.2.tgz) |
| @typescript/typescript-linux-mips64el | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-linux-mips64el/-/typescript-linux-mips64el-7.0.2.tgz) |
| @typescript/typescript-linux-ppc64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-linux-ppc64/-/typescript-linux-ppc64-7.0.2.tgz) |
| @typescript/typescript-linux-riscv64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-linux-riscv64/-/typescript-linux-riscv64-7.0.2.tgz) |
| @typescript/typescript-linux-s390x | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-linux-s390x/-/typescript-linux-s390x-7.0.2.tgz) |
| @typescript/typescript-linux-x64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-linux-x64/-/typescript-linux-x64-7.0.2.tgz) |
| @typescript/typescript-netbsd-arm64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-netbsd-arm64/-/typescript-netbsd-arm64-7.0.2.tgz) |
| @typescript/typescript-netbsd-x64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-netbsd-x64/-/typescript-netbsd-x64-7.0.2.tgz) |
| @typescript/typescript-openbsd-arm64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-openbsd-arm64/-/typescript-openbsd-arm64-7.0.2.tgz) |
| @typescript/typescript-openbsd-x64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-openbsd-x64/-/typescript-openbsd-x64-7.0.2.tgz) |
| @typescript/typescript-sunos-x64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-sunos-x64/-/typescript-sunos-x64-7.0.2.tgz) |
| @typescript/typescript-win32-arm64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-win32-arm64/-/typescript-win32-arm64-7.0.2.tgz) |
| @typescript/typescript-win32-x64 | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@typescript/typescript-win32-x64/-/typescript-win32-x64-7.0.2.tgz) |
| @vitejs/plugin-react | 6.1.2 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@vitejs/plugin-react/-/plugin-react-6.1.2.tgz) |
| @vitest/mocker | 5.0.5 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@vitest/mocker/-/mocker-5.0.5.tgz) |
| @vitest/spy | 5.0.5 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/@vitest/spy/-/spy-5.0.5.tgz) |
| assertion-error | 2.0.1 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/assertion-error/-/assertion-error-2.0.1.tgz) |
| chai | 6.3.0 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/chai/-/chai-6.3.0.tgz) |
| csstype | 3.2.3 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/csstype/-/csstype-3.2.3.tgz) |
| detect-libc | 2.1.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/detect-libc/-/detect-libc-2.1.2.tgz) |
| es-module-lexer | 2.3.2 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/es-module-lexer/-/es-module-lexer-2.3.2.tgz) |
| estree-walker | 3.0.3 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/estree-walker/-/estree-walker-3.0.3.tgz) |
| expect-type | 1.4.0 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/expect-type/-/expect-type-1.4.0.tgz) |
| fdir | 6.5.0 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/fdir/-/fdir-6.5.0.tgz) |
| fsevents | 2.3.3 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/fsevents/-/fsevents-2.3.3.tgz) |
| lightningcss | 1.33.0 | MPL-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/lightningcss/-/lightningcss-1.33.0.tgz) |
| lightningcss-android-arm64 | 1.33.0 | MPL-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/lightningcss-android-arm64/-/lightningcss-android-arm64-1.33.0.tgz) |
| lightningcss-darwin-arm64 | 1.33.0 | MPL-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/lightningcss-darwin-arm64/-/lightningcss-darwin-arm64-1.33.0.tgz) |
| lightningcss-darwin-x64 | 1.33.0 | MPL-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/lightningcss-darwin-x64/-/lightningcss-darwin-x64-1.33.0.tgz) |
| lightningcss-freebsd-x64 | 1.33.0 | MPL-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/lightningcss-freebsd-x64/-/lightningcss-freebsd-x64-1.33.0.tgz) |
| lightningcss-linux-arm-gnueabihf | 1.33.0 | MPL-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/lightningcss-linux-arm-gnueabihf/-/lightningcss-linux-arm-gnueabihf-1.33.0.tgz) |
| lightningcss-linux-arm64-gnu | 1.33.0 | MPL-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/lightningcss-linux-arm64-gnu/-/lightningcss-linux-arm64-gnu-1.33.0.tgz) |
| lightningcss-linux-arm64-musl | 1.33.0 | MPL-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/lightningcss-linux-arm64-musl/-/lightningcss-linux-arm64-musl-1.33.0.tgz) |
| lightningcss-linux-x64-gnu | 1.33.0 | MPL-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/lightningcss-linux-x64-gnu/-/lightningcss-linux-x64-gnu-1.33.0.tgz) |
| lightningcss-linux-x64-musl | 1.33.0 | MPL-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/lightningcss-linux-x64-musl/-/lightningcss-linux-x64-musl-1.33.0.tgz) |
| lightningcss-win32-arm64-msvc | 1.33.0 | MPL-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/lightningcss-win32-arm64-msvc/-/lightningcss-win32-arm64-msvc-1.33.0.tgz) |
| lightningcss-win32-x64-msvc | 1.33.0 | MPL-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/lightningcss-win32-x64-msvc/-/lightningcss-win32-x64-msvc-1.33.0.tgz) |
| magic-string | 1.4.5 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/magic-string/-/magic-string-1.4.5.tgz) |
| nanoid | 3.3.20 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/nanoid/-/nanoid-3.3.20.tgz) |
| obug | 3.0.0 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/obug/-/obug-3.0.0.tgz) |
| picocolors | 1.1.1 | ISC | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/picocolors/-/picocolors-1.1.1.tgz) |
| picomatch | 4.0.7 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/picomatch/-/picomatch-4.0.7.tgz) |
| postcss | 8.5.32 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/postcss/-/postcss-8.5.32.tgz) |
| rolldown | 1.2.16 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/rolldown/-/rolldown-1.2.16.tgz) |
| source-map-js | 1.2.2 | BSD-3-Clause | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/source-map-js/-/source-map-js-1.2.2.tgz) |
| std-env | 4.3.0 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/std-env/-/std-env-4.3.0.tgz) |
| tinybench | 6.2.2 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/tinybench/-/tinybench-6.2.2.tgz) |
| tinyexec | 1.3.1 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/tinyexec/-/tinyexec-1.3.1.tgz) |
| tinyglobby | 0.2.17 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/tinyglobby/-/tinyglobby-0.2.17.tgz) |
| typescript | 7.0.2 | Apache-2.0 | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/typescript/-/typescript-7.0.2.tgz) |
| undici-types | 6.21.0 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/undici-types/-/undici-types-6.21.0.tgz) |
| vite | 8.3.5 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/vite/-/vite-8.3.5.tgz) |
| vitest | 5.0.5 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/vitest/-/vitest-5.0.5.tgz) |
| why-is-node-running | 3.2.1 | MIT | apps/web/package-lock.json | [artifact](https://registry.npmjs.org/why-is-node-running/-/why-is-node-running-3.2.1.tgz) |
