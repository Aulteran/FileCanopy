"""Request/response models. Responses are explicit allow-lists: no ORM model is returned
directly, so a password hash or token digest can never leak by adding a column."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import User


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    status: str
    created_at: datetime

    @classmethod
    def of(cls, u: User) -> "UserOut":
        return cls(
            id=u.id,
            email=u.email,
            name=u.name,
            role=u.role,
            status=u.status,
            created_at=u.created_at,
        )


class SetupStatus(BaseModel):
    setup_required: bool


class SetupAdmin(Strict):
    setup_token: str = Field(max_length=512)
    email: str = Field(max_length=320)
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(max_length=1024)


class Login(Strict):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


class SessionOut(BaseModel):
    user: UserOut
    csrf_token: str
    locked: bool = False  # the lock screen is up (M12b)
    pin_set: bool = False


class ChangePassword(Strict):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


class AcceptInvitation(Strict):
    token: str = Field(max_length=256)
    password: str = Field(max_length=1024)
    name: str | None = Field(default=None, min_length=1, max_length=200)


class CompleteReset(Strict):
    token: str = Field(max_length=256)
    password: str = Field(max_length=1024)


class AdminUserOut(UserOut):
    sso_linked: bool = False


class CreateUser(Strict):
    email: str = Field(max_length=320)
    name: str = Field(min_length=1, max_length=200)
    role: Literal["admin", "member"] = "member"
    send_email: bool = False


class SendOptions(Strict):
    send_email: bool = False


class UpdateUser(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Literal["admin", "member"] | None = None
    status: Literal["active", "disabled"] | None = None


class TokenOut(BaseModel):
    """The one-time secret is returned only here, at issuance; it is not stored in plaintext."""

    token: str
    purpose: str
    expires_at: datetime


class IssuedToken(BaseModel):
    """`token` is present for a manual link and absent when the link was emailed: the
    administrator never sees a link that was sent to the person."""

    token: str | None
    purpose: str
    expires_at: datetime
    delivery: Literal["manual", "email_sent", "email_failed"]


class UserWithToken(BaseModel):
    user: UserOut
    token: IssuedToken


class ForgotPassword(Strict):
    email: str = Field(max_length=320)


class OidcStartLink(Strict):
    password: str = Field(max_length=1024)


class OidcStartInvite(Strict):
    token: str = Field(max_length=256)
    # Omitted only in OIDC-only mode: the account then has no password at all.
    password: str | None = Field(default=None, max_length=1024)
    name: str | None = Field(default=None, min_length=1, max_length=200)


class AuthorizationUrl(BaseModel):
    authorization_url: str


class OidcPublic(BaseModel):
    enabled: bool
    label: str


class AuthConfig(BaseModel):
    oidc: OidcPublic
    password_reset_by_email: bool
    # False while the OIDC-only mode is in force: the sign-in page then offers the provider only.
    password_login: bool = True


class IdentityOut(BaseModel):
    linked: bool
    label: str | None = None


class Ok(BaseModel):
    status: Literal["ok"] = "ok"
