"""Read installed distribution evidence, not inferred package-name licences.

Run via stdin in the exact API runtime image, with --network none.
"""

import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path

packages = []
for dist in metadata.distributions():
    files = []
    for item in dist.files or []:
        if any(word in str(item).lower() for word in ("license", "licence", "copying", "notice")):
            path = Path(dist.locate_file(item))
            if path.is_file():
                data = path.read_bytes()
                files.append({"path": str(item), "sha256": hashlib.sha256(data).hexdigest(),
                              "text": data.decode("utf-8", errors="replace")})
    packages.append({
        "name": dist.metadata["Name"], "version": dist.version,
        "license_expression": dist.metadata.get("License-Expression"),
        "license_field": dist.metadata.get("License"),
        "license_classifiers": [c for c in dist.metadata.get_all("Classifier", []) if "License" in c],
        "requires_dist": dist.requires or [], "license_files": files,
        "metadata_sha256": hashlib.sha256(dist.read_text("METADATA").encode()).hexdigest(),
    })
print(json.dumps(sorted(packages, key=lambda p: p["name"].lower()), indent=2))
