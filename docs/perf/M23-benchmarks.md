# M23 benchmarks

Reference run for `tests/perf/test_benchmarks.py` (`FILECANOPY_PERF=1 pytest tests/perf -s`).

**What this is.** One process, in-memory SQLite, synthetic names, a 20,000-file tree (20 folders of
1,000 files) in one person's folder, run in the API dev image on a developer laptop (Docker
Desktop, Windows 11). **What it is not.** A PostgreSQL measurement behind nginx, a statement about
any particular NAS, or a promise. The budgets in the test are deliberately loose (seconds) so they
catch an order-of-magnitude regression, not noise. The multiuser PostgreSQL behaviour is tested
in `tests/integration/compose/test_hardening_stack.py`.

| Operation | Size | Median |
|---|---|---|
| List one folder (`GET /nodes/{id}/children`) | 1,000 entries | 10 ms |
| Filename search (`GET /search`) | 20,000 files | 19 ms |
| `filter_visible` (owner) | 200 ids | 101 ms |
| `filter_visible` (owner) | 2,000 ids | 997 ms |
| `filter_visible` (person with no access) | 2,000 ids | 1,048 ms |

## Reading the numbers

- Listing and search stay in milliseconds at this size.
- `filter_visible` costs about 0.5 ms per id on this setup and grows linearly with the number of
  ids (ten times the ids, about ten times the time; the test fails if it ever grows faster). It is
  called per reference by collections, search hits and notifications, so a surface that passes
  thousands of ids through it pays about half a second per thousand. That is the M08 "N8" follow-up:
  it is **measured and linear, not yet optimised**. The obvious optimisation (resolve the actor's
  grants once per request instead of per id) is a behaviour-neutral refactor of
  `app/policy/engine.py` and is left for a change with its own review, because that module is the
  centre of the access model.
