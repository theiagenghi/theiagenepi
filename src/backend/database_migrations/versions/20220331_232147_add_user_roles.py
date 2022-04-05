"""add user roles

Create Date: 2022-03-31 23:21:53.865128

"""
import enumtables  # noqa: F401
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20220331_232147"
down_revision = "20220315_205645"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "roles",
        sa.Column(
            "id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False
        ),
        sa.Column("name", sa.VARCHAR(), nullable=False, comment="role name"),
        schema="aspen",
    )
    op.create_unique_constraint("uq_roles_name", "roles", ["name"], schema="aspen")
    op.create_table(
        "group_roles",
        sa.Column(
            "role_id",
            sa.INTEGER(),
            sa.ForeignKey("aspen.roles.id"),
            index=True,
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.INTEGER(),
            sa.ForeignKey("aspen.users.id"),
            index=True,
            autoincrement=False,
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "group_id",
            sa.INTEGER(),
            sa.ForeignKey("aspen.groups.id"),
            index=True,
            autoincrement=False,
            nullable=False,
            primary_key=True,
        ),
        schema="aspen",
    )


def downgrade():
    pass
