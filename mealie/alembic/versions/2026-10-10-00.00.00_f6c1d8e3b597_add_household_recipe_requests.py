"""add household recipe requests

Revision ID: f6c1d8e3b597
Revises: e5b9c3d7a248
Create Date: 2026-10-10 00:00:00.000000

One row per "find a new recipe" press on the Recipes page: who asked, an optional note about what
they are in the mood for, and where the request got to (pending, filling, filled or failed). The
recipe that answered it is a snapshot, not a foreign key, so deleting that recipe later never has
to touch this table.
"""

import sqlalchemy as sa
from alembic import op

import mealie.db.migration_types

# revision identifiers, used by Alembic.
revision = "f6c1d8e3b597"
down_revision: str | None = "e5b9c3d7a248"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade():
    op.create_table(
        "household_recipe_requests",
        sa.Column("id", mealie.db.migration_types.GUID(), nullable=False),
        sa.Column("group_id", mealie.db.migration_types.GUID(), nullable=True),
        sa.Column("household_id", mealie.db.migration_types.GUID(), nullable=True),
        sa.Column("requested_by", mealie.db.migration_types.GUID(), nullable=True),
        sa.Column("requested_by_name", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("recipe_id", mealie.db.migration_types.GUID(), nullable=True),
        sa.Column("recipe_slug", sa.String(length=255), nullable=True),
        sa.Column("recipe_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", mealie.db.migration_types.NaiveDateTime(), nullable=True),
        sa.Column("update_at", mealie.db.migration_types.NaiveDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["households.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("household_recipe_requests", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_household_recipe_requests_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_household_recipe_requests_group_id"), ["group_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_household_recipe_requests_household_id"), ["household_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_household_recipe_requests_status"), ["status"], unique=False)


def downgrade():
    with op.batch_alter_table("household_recipe_requests", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_household_recipe_requests_status"))
        batch_op.drop_index(batch_op.f("ix_household_recipe_requests_household_id"))
        batch_op.drop_index(batch_op.f("ix_household_recipe_requests_group_id"))
        batch_op.drop_index(batch_op.f("ix_household_recipe_requests_created_at"))

    op.drop_table("household_recipe_requests")
