# policy

The single authorization service. Every API path, job, preview, search, audit, collection, MCP and email action must call into this module; roles never imply content access. Implemented in M03 (see [ADR-004](../../../../docs/adr/ADR-004-acl-and-admin-separation.md)).

- `model.py`: `Privilege` (traverse, list, read, write, share, manage), `Actor` (an account id, never a role).
- `engine.py`: `effective(db, actor, node)` / `decide()` / `require()` evaluate owner and grant privileges from the node's lineage on every call (no cache, so revocation is immediate); `can_administer_grants()`; `entry_points()`. Missing and unauthorized nodes both raise `Forbidden`.
- `deps.py`: FastAPI guards `traverse_access` and `grant_admin_access`. Every `{node_id}` route must depend on one (`policy_guard`); `tests/unit/api/test_acl.py::test_every_node_route_depends_on_a_policy_guard` fails for any new route that does not. Denials are audited with ids only.

New code that reads or changes node content (M04+) adds its privilege check here, not in the route.
