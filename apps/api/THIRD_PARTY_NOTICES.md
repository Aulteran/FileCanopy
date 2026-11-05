# API third-party notices and sources

FileCanopy uses Psycopg and psycopg-binary 3.3.6 under LGPL-3.0-only.
Their installed licence files are in the respective `.dist-info/licenses`
directories under `/usr/local/lib/python3.13/site-packages`. The LGPL incorporates
GPL v3; both texts must accompany a release. The application's AGPL text is at
`/srv/filecanopy/LICENSE`. Third-party components retain their own terms.
Modification of the LGPL libraries and reverse engineering to debug those
modifications are not restricted by FileCanopy. Distribution must preserve a
working library replacement/rebuild route; merely installing a binary wheel does
not establish that this requirement has been met.

`psycopg-binary` includes libpq 18.6 and OpenSSL 3.5.8 in the inspected Linux amd64
artifact. `cryptography` 50.0.2 contains another OpenSSL, 4.0.3. Their additional
notices are under `vendor-notices/`. This is a partial native notice assembly:
Kerberos, LDAP, SASL, crypt/SELinux and cryptography's Rust dependencies still need
exact-source and notice reconciliation. Pillow 13.0.0's installed licence includes
its native third-party notices. Do not infer all native versions from OS packages.

Certifi 2026.10.23 is MPL-2.0. Its exact unmodified source distribution, including
`certifi/cacert.pem`, licence and build metadata, is provided with this image at
`/srv/filecanopy/apps/api/vendor-sources/certifi-2026.10.23.tar.gz`.
SHA-256: `a9895e6e2758b371e0545e321a3a8fd1fcfd9829ada8824a936dfb16aaaf36cd`.
It can be copied out with `docker cp` from a stopped container created from the
image; no application account or network access is needed. The original PyPI
artifact URL/hash is retained in the release evidence. MPL terms and copyright
notices are preserved in the archive and installed package. Pip 26.2.2 vendors
certifi **2026.10.19**, a separate copy. Its exact source, modifications, vendor
licences and build materials accompany this image in
`/srv/filecanopy/apps/api/vendor-sources/pip-26.2.2.tar.gz` (SHA-256
`3a8e5c800492787f33a4114ef54444546c340df428974ebba17e0fd47e11af2f`).
The source archive's `src/pip/_vendor` contents are byte-compared with installed
vendor files by the artifact verification script. The installed vendor licence
files remain in pip's package and `.dist-info/licenses` directories.

This file is an inventory/source notice, not legal sign-off. Before publication,
finish the exact-image source and notice checklist in `docs/release/source-delivery.md`
in the corresponding repository checkout. An upstream homepage or wrapper sdist
alone does not supply source for vendored native binaries.
