"""add invitations table

Create Date: 2026-07-20 12:00:00.000000

"""
import enumtables  # noqa: F401
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260720_120000"
down_revision = "20240816_223757"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invitation_status_types",
        sa.Column("item_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_invitation_status_types")),
        schema="aspen",
    )
    op.enum_insert(
        "invitation_status_types",
        ["PENDING", "ACCEPTED", "REVOKED"],
        schema="aspen",
    )
    op.create_table(
        "invitations",
        sa.Column(
            "id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False
        ),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("aspen.groups.id"),
            index=True,
            nullable=False,
        ),
        sa.Column(
            "role_id", sa.Integer(), sa.ForeignKey("aspen.roles.id"), nullable=False
        ),
        sa.Column(
            "invited_by_user_id",
            sa.Integer(),
            sa.ForeignKey("aspen.users.id"),
            nullable=False,
        ),
        sa.Column("invitee_email", sa.String(), index=True, nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("status", enumtables.enum_column.EnumType(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        schema="aspen",
    )
    op.create_unique_constraint(
        op.f("uq_invitations_token_hash"),
        "invitations",
        ["token_hash"],
        schema="aspen",
    )
    op.create_foreign_key(
        op.f("fk_invitations_status_invitation_status_types"),
        "invitations",
        "invitation_status_types",
        ["status"],
        ["item_id"],
        source_schema="aspen",
        referent_schema="aspen",
    )


def downgrade():
    op.drop_table("invitations", schema="aspen")
    op.drop_table("invitation_status_types", schema="aspen")
