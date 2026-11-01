"""Builds an OpenDocument text file with an auto-run Basic macro (on document load) that writes a
marker file. Used to check that the converter never lets a document run code."""

import io
import zipfile

MODULE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE script:module PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "module.dtd">
<script:module xmlns:script="http://openoffice.org/2000/script" script:name="Module1"
 script:language="StarBasic">Sub Main
  Dim n As Integer
  n = FreeFile
  Open "{marker}" For Output As #n
  Print #n, "macro ran"
  Close #n
End Sub
</script:module>"""
LISTENER = (
    '<script:event-listener script:language="ooo:script" script:event-name="{event}" '
    'xlink:href="vnd.sun.star.script:Standard.Module1.Main?language=Basic&amp;location=document" '
    'xlink:type="simple"/>'
)
LIBRARIES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE library:libraries PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" '
    '"libraries.dtd">\n<library:libraries xmlns:library="http://openoffice.org/2000/library" '
    'xmlns:xlink="http://www.w3.org/1999/xlink"><library:library library:name="Standard" '
    'library:link="false"/></library:libraries>'
)
LIBRARY = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE library:library PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" '
    '"library.dtd">\n<library:library xmlns:library="http://openoffice.org/2000/library" '
    'library:name="Standard" library:readonly="false" library:passwordprotected="false">'
    '<library:element library:name="Module1"/></library:library>'
)
ENTRIES = [
    ("Basic/", "application/binary"),
    ("Basic/Standard/", "application/binary"),
    ("Basic/script-lc.xml", "text/xml"),
    ("Basic/Standard/script-lb.xml", "text/xml"),
    ("Basic/Standard/Module1.xml", "text/xml"),
]


def make_macro_odt(plain_odt: bytes, marker: str) -> bytes:
    """`plain_odt` is a real ODT made by LibreOffice; the macro and its triggers are added."""
    files = {}
    with zipfile.ZipFile(io.BytesIO(plain_odt)) as z:
        for name in z.namelist():
            files[name] = z.read(name)
    listeners = "".join(LISTENER.format(event=e) for e in ("office:load", "dom:load"))
    content = files["content.xml"].decode()
    content = content.replace(
        "<office:scripts/>",
        f"<office:scripts><office:event-listeners>{listeners}</office:event-listeners>"
        "</office:scripts>",
    )
    files["content.xml"] = content.encode()
    files["Basic/script-lc.xml"] = LIBRARIES.encode()
    files["Basic/Standard/script-lb.xml"] = LIBRARY.encode()
    files["Basic/Standard/Module1.xml"] = MODULE.format(marker=marker).encode()
    extra = "".join(
        f'<manifest:file-entry manifest:full-path="{p}" manifest:media-type="{t}"/>'
        for p, t in ENTRIES
    )
    files["META-INF/manifest.xml"] = (
        files["META-INF/manifest.xml"]
        .decode()
        .replace("</manifest:manifest>", extra + "</manifest:manifest>")
    ).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), files.pop("mimetype"))
        for name, data in files.items():
            z.writestr(name, data, zipfile.ZIP_DEFLATED)
    return out.getvalue()
