# storage

The only module that touches document filesystem paths. It accepts node identifiers (turned into names from the database lineage), never raw client paths.

Implemented in M03:

- `names.py`: `validate_name()` (NFC; rejects empty, `.`, `..`, separators, NUL/control characters, >255 UTF-8 bytes).
- `resolver.py`: `open_dir()` walks `<library>/<root relative_dir>/<names...>` one component at a time with `O_NOFOLLOW | O_DIRECTORY` relative to the parent descriptor. Symlinks at any depth (the root directory included) raise `StorageEscape`; a filesystem boundary below a root is refused; names are validated again at use time. `ensure_root_dir()` creates roots with mode `0o750`.
- `roots.py`: `ensure_roots()` runs at API start, creates missing root directories and marks unusable ones `missing`.

Not yet: listing, create, upload, move, copy (M04). Directories for accounts created while the API is running appear at the next start; M04 write paths must call `ensure_root_dir()` before first use.
