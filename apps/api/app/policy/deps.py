"""FastAPI guards. Every node-addressed route depends on one of these (enforced by the route
inventory test), so it cannot skip the policy. Denials are uniform and audited with ids only."""

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.deps import Principal, client_ip, current_principal, get_db, http_error
from app.auth.service import audit
from app.db.models import FileOperation, Node
from app.policy import engine
from app.policy.model import Actor, Privilege


@dataclass
class NodeContext:
    principal: Principal
    node: Node
    privileges: frozenset[Privilege]


def _deny(
    db: Session, request: Request, principal: Principal, node_id: uuid.UUID, what: str
) -> HTTPException:
    audit(
        db,
        "access.denied",
        actor=principal.user.id,
        ip=client_ip(request),
        node_id=str(node_id),
        required=what,
    )
    db.commit()
    return http_error(403, "FORBIDDEN")


class NodeAccess:
    """Dependency factory: `Depends(NodeAccess(Privilege.TRAVERSE))` on a `{node_id}` route."""

    policy_guard = True

    def __init__(self, privilege: Privilege) -> None:
        self.privilege = privilege

    def __call__(
        self,
        node_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(current_principal),
        db: Session = Depends(get_db),
    ) -> NodeContext:
        actor = Actor(principal.user.id)
        node = db.get(Node, node_id)
        privileges = engine.effective(db, actor, node)
        if node is None or self.privilege not in privileges:
            raise _deny(db, request, principal, node_id, self.privilege.value)
        return NodeContext(principal, node, privileges)


class GrantAdminAccess:
    """Guard for grant management: owner of a personal folder, or administrator in the shared
    tree. Unauthorized and nonexistent nodes are indistinguishable."""

    policy_guard = True

    def __call__(
        self,
        node_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(current_principal),
        db: Session = Depends(get_db),
    ) -> NodeContext:
        actor = Actor(principal.user.id)
        node = db.get(Node, node_id)
        if node is None or not engine.can_administer_grants(db, actor, node):
            raise _deny(db, request, principal, node_id, "grants")
        return NodeContext(principal, node, engine.effective(db, actor, node))


class OperationAccess:
    """Guard for journal rows: visible to the actor who started the operation only. Other
    people's and nonexistent ids are indistinguishable."""

    policy_guard = True

    def __call__(
        self,
        operation_id: uuid.UUID,
        request: Request,
        principal: Principal = Depends(current_principal),
        db: Session = Depends(get_db),
    ) -> FileOperation:
        op = db.get(FileOperation, operation_id)
        if op is None or op.actor_user_id != principal.user.id:
            raise _deny(db, request, principal, operation_id, "operation")
        return op


# Module-level singletons: FastAPI dependencies are cached by identity, and route signatures stay
# free of calls in argument defaults.
traverse_access = NodeAccess(Privilege.TRAVERSE)
list_access = NodeAccess(Privilege.LIST)
read_access = NodeAccess(Privilege.READ)
write_access = NodeAccess(Privilege.WRITE)
grant_admin_access = GrantAdminAccess()
