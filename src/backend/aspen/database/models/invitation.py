from __future__ import annotations

import enum
import hashlib
import secrets
from datetime import datetime, timedelta

import enumtables
from sqlalchemy import Column, DateTime, ForeignKey, func, Integer, String
from sqlalchemy.orm import backref, relationship

from aspen.database.models.base import base, idbase
from aspen.database.models.enum import Enum
from aspen.database.models.usergroup import Group, Role, User

DEFAULT_EXPIRY_DAYS = 14


class InvitationStatusType(enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REVOKED = "REVOKED"


_InvitationStatusTypeTable = enumtables.EnumTable(
    InvitationStatusType,
    base,
    tablename="invitation_status_types",
)


def generate_invitation_token() -> str:
    return secrets.token_urlsafe(32)


def hash_invitation_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def default_expires_at() -> datetime:
    return datetime.now() + timedelta(days=DEFAULT_EXPIRY_DAYS)


class Invitation(idbase):  # type: ignore
    """An outstanding invitation for someone to join a group.

    Replaces Auth0's organization invitations. Only the SHA-256 hash of the
    invitation token is stored, so a database leak does not hand out usable
    invitation links; the raw token exists only in the emailed URL.
    """

    __tablename__ = "invitations"

    group_id = Column(Integer, ForeignKey(Group.id), nullable=False, index=True)
    group = relationship(Group, backref=backref("invitations", uselist=True))  # type: ignore

    role_id = Column(Integer, ForeignKey(Role.id), nullable=False)
    role = relationship(Role)  # type: ignore

    invited_by_user_id = Column(Integer, ForeignKey(User.id), nullable=False)
    invited_by = relationship(User, foreign_keys=[invited_by_user_id])  # type: ignore

    invitee_email = Column(String, nullable=False, index=True)
    token_hash = Column(String, unique=True, nullable=False)

    status = Column(
        Enum(InvitationStatusType),
        ForeignKey(_InvitationStatusTypeTable.item_id),
        nullable=False,
        default=InvitationStatusType.PENDING,
    )

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    expires_at = Column(DateTime, nullable=False, default=default_expires_at)
    accepted_at = Column(DateTime, nullable=True)

    def is_expired(self) -> bool:
        return self.expires_at < datetime.now()

    def is_redeemable(self) -> bool:
        return self.status == InvitationStatusType.PENDING and not self.is_expired()

    def __repr__(self):
        return f"Invitation <{self.invitee_email} -> group {self.group_id}>"
