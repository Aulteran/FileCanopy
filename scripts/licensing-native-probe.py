#!/usr/bin/env python3
"""Offline artifact probe: pipe into the exact API image, retain stdout with image ID."""

import ctypes
import hashlib
import json
import sys
from importlib import metadata
from pathlib import Path

import PIL.features
import psycopg.pq
from cryptography.hazmat.backends.openssl.backend import backend
from pip._vendor import certifi as pip_certifi

result = {
    "python": sys.version,
    "psycopg": metadata.version("psycopg"),
    "pip": metadata.version("pip"),
    "pip_certifi": pip_certifi.__version__,
    "libpq_version": psycopg.pq.version(),
    "cryptography": metadata.version("cryptography"),
    "cryptography_openssl": backend.openssl_version_text(),
    "pillow": metadata.version("Pillow"),
    "pillow_modules": {
        n: PIL.features.version_module(n) for n in PIL.features.get_supported_modules()
    },
    "pillow_codecs": {
        n: PIL.features.version_codec(n) for n in PIL.features.get_supported_codecs()
    },
    "pillow_features": {
        n: PIL.features.version_feature(n)
        for n in PIL.features.get_supported_features()
    },
    "vendored_libraries": [],
}
for directory in ("psycopg_binary.libs", "pillow.libs"):
    for path in sorted(
        Path("/usr/local/lib").glob("python*/site-packages/" + directory + "/*")
    ):
        item = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if path.name.startswith("libssl"):
            lib = ctypes.CDLL(str(path))
            lib.OpenSSL_version.restype = ctypes.c_char_p
            item["openssl_version"] = lib.OpenSSL_version(0).decode()
        result["vendored_libraries"].append(item)
print(json.dumps(result, indent=2))
