# storage

The only module that touches document filesystem paths. It accepts node identifiers (turned into names from the database lineage), never raw client paths.

Implemented in M03:

- `names.py`: `validate_name()` (NFC; rejects empty, `.`, `..`, separators, NUL/control characters, >255 UTF-8 bytes).
- `resolver.py`: `open_dir()` walks `<library>/<root relative_dir>/<names...>` one component at a time with `O_NOFOLLOW | O_DIRECTORY` relative to the parent descriptor. Symlinks at any depth (the root directory included) raise `StorageEscape`; a filesystem boundary below a root is refused; names are validated again at use time. `ensure_root_dir()` creates roots with mode `0o750`.
- `roots.py`: `ensure_roots()` runs at API start, creates missing root directories and marks unusable ones `missing`.

Implemented in M04 (`files.py`, ADR-013): directory-fd primitives for file operations. `rename_noreplace()` (renameat2 `RENAME_NOREPLACE`, `link`+`unlink` fallback), `StagedWrite` (temporary file in the destination directory, `fsync`, no-replace publish), `open_regular()` (`O_NOFOLLOW | O_NONBLOCK`, regular single-link files only), `scan_tree()` / `copy_tree()` / `remove_verified_source()` for copy and cross-device moves. Nothing here builds a path string. Write paths in `domain/fileops.py` call `ensure_root_dir()` first, so accounts created while the API runs get their directory on first write.
