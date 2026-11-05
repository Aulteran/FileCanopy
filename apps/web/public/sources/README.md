# Sources for distributed fonts

The four `/pdfjs/standard_fonts/LiberationSans-*.ttf` fonts distributed with PDF.js
6.4.299 are Liberation Sans 1.07.4, identified by that release's upstream
[font README](https://github.com/mozilla/pdf.js/blob/v6.4.299/external/standard_fonts/README.md).
Their full GPL v2 terms and exceptions are at `/pdfjs/standard_fonts/LICENSE_LIBERATION`.

Their editable SFD sources, build scripts, copyright notices and licence are provided
alongside the fonts in [liberation-1.07.4-dbeb786b.tar.gz](liberation-1.07.4-dbeb786b.tar.gz).
This is the unmodified archive of upstream release commit
`dbeb786b4ede1eaec2ec5cb8d3b75d21641e2a35` ("Commit for 1.07.4 release"); its
Makefile declares `VER = 1.07.4`. SHA-256:
`05a8cc739f41d33fbc6d18f83f597cbebb91725bba028d7f1e36f7a211173abd`.

Extract the archive and follow its README and Makefile; the build uses FontForge
and GNU make. Font modifications retain the applicable licence, exception and
trademark conditions. The archive preserves the upstream source, including fonts
that FileCanopy does not distribute as binaries. No identical-byte rebuild has
been demonstrated. No newer Liberation font has been substituted.

This directory supplies font sources; the complete application and other native
components require their own exact-build source materials. See the repository's
release licensing review before making a release-clearance claim.
