"""add household calendar sync

Revision ID: a7e2d4f9c6b3
Revises: f6c1d8e3b597
Create Date: 2026-11-10 00:00:00.000000

One row per household that mirrors its meal plan to a Google calendar: which calendar, whether the
mirror is on, and how the last sync went. Nothing about individual events is stored; each calendar
event says which plan entry it belongs to, so the table never has to be reconciled with Google.
"""

import sqlalchemy as sa
from alembic import op

import mealie.db.migration_types

# revision identifiers, used by Alembic.
revision = "a7e2d4f9c6b3"
down_revision: str | None = "f6c1d8e3b597"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade():
    op.create_table(
        "household_calendar_sync",
        sa.Column("id", mealie.db.migration_types.GUID(), nullable=False),
        sa.Column("group_id", mealie.db.migration_types.GUID(), nullable=True),
        sa.Column("household_id", mealie.db.migration_types.GUID(), nullable=True),
        sa.Column("calendar_id", sa.String(length=1024), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_synced_at", mealie.db.migration_types.NaiveDateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
    with op.batch_alter_table("household_calendar_sync", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_household_calendar_sync_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_household_calendar_sync_enabled"), ["enabled"], unique=False)
        batch_op.create_index(batch_op.f("ix_household_calendar_sync_group_id"), ["group_id"], unique=False)
        # unique: a household mirrors to one calendar, so it has one row
        batch_op.create_index(batch_op.f("ix_household_calendar_sync_household_id"), ["household_id"], unique=True)


def downgrade():
    with op.batch_alter_table("household_calendar_sync", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_household_calendar_sync_household_id"))
        batch_op.drop_index(batch_op.f("ix_household_calendar_sync_group_id"))
        batch_op.drop_index(batch_op.f("ix_household_calendar_sync_enabled"))
        batch_op.drop_index(batch_op.f("ix_household_calendar_sync_created_at"))

    op.drop_table("household_calendar_sync")
