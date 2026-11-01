"""Previews and safe extraction (M10, ADR-019): derivatives of files, never the files themselves.

Everything here reads a file the access guard already opened and writes only under
`app-data/previews/`. Parsing of untrusted bytes happens in a limited child process
(`sandbox`, `worker`); Office conversion happens in the optional sealed converter container
(`convert`). See docs/milestones/M10-design-and-plan.md.
"""
