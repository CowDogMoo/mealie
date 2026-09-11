"""add household recipe sources

Revision ID: d4a8b2c6e9f1
Revises: c3f7a91e5d20
Create Date: 2026-09-11 00:00:00.000000

One row per (household, domain): the household's current judgement about a recipe site --
known-good, caution or blocked -- plus a free-text note. The status is state, not a log; the
reasons behind a demotion are already recorded as `user_recipe_feedback` events.
"""

import sqlalchemy as sa
from alembic import op

import mealie.db.migration_types

# revision identifiers, used by Alembic.
revision = "d4a8b2c6e9f1"
down_revision: str | None = "c3f7a91e5d20"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade():
    op.create_table(
        "household_recipe_sources",
        sa.Column("id", mealie.db.migration_types.GUID(), nullable=False),
        sa.Column("group_id", mealie.db.migration_types.GUID(), nullable=True),
        sa.Column("household_id", mealie.db.migration_types.GUID(), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("household_id", "domain", name="household_recipe_sources_household_domain_key"),
    )
    with op.batch_alter_table("household_recipe_sources", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_household_recipe_sources_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_household_recipe_sources_domain"), ["domain"], unique=False)
        batch_op.create_index(batch_op.f("ix_household_recipe_sources_group_id"), ["group_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_household_recipe_sources_household_id"), ["household_id"], unique=False)


def downgrade():
    with op.batch_alter_table("household_recipe_sources", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_household_recipe_sources_household_id"))
        batch_op.drop_index(batch_op.f("ix_household_recipe_sources_group_id"))
        batch_op.drop_index(batch_op.f("ix_household_recipe_sources_domain"))
        batch_op.drop_index(batch_op.f("ix_household_recipe_sources_created_at"))

    op.drop_table("household_recipe_sources")
