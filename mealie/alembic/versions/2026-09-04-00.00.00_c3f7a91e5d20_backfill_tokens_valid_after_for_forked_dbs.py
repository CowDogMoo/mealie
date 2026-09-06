"""backfill tokens_valid_after for databases migrated by the pre-rebase fork

Revision ID: c3f7a91e5d20
Revises: b7d3a1f0c2e4
Create Date: 2026-09-04 00:00:00.000000

The fork's own migration, b7d3a1f0c2e4, was first released with
`down_revision = "2187537c52b8"`. Rebasing onto upstream moved it behind
69e942bab3aa, which is the migration that adds `users.tokens_valid_after`.

Any database migrated by that first release is therefore stamped at
b7d3a1f0c2e4 without ever having run 69e942bab3aa. Because b7d3a1f0c2e4 is head
in the rebased graph, `db_is_at_head` reports it as current and init_db skips
the upgrade entirely, so the column is never created. Every authenticated
request reads it (see mealie/core/dependencies/dependencies.py), so the app
fails on the first request after such a database is deployed.

Reordering the two revisions would fix that database and break the opposite
one: a database created after the rebase already has the column, and rerunning
69e942bab3aa against it would fail on a duplicate column. So this revision is
additive and checks before it writes, which leaves every combination of
migration history in the same, correct place.

Restored backups are the reason this lives in a migration instead of a one-off
repair of the running database: a restore returns the database to b7d3a1f0c2e4,
and the repair has to still be there when it does.
"""

import sqlalchemy as sa
from alembic import op

import mealie.db.migration_types

# revision identifiers, used by Alembic.
revision = "c3f7a91e5d20"
down_revision: str | None = "b7d3a1f0c2e4"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def _users_has_tokens_valid_after() -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(column["name"] == "tokens_valid_after" for column in inspector.get_columns("users"))


def upgrade():
    if _users_has_tokens_valid_after():
        return

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tokens_valid_after", mealie.db.migration_types.NaiveDateTime(), nullable=True))


def downgrade():
    # 69e942bab3aa owns this column in the canonical graph; dropping it here would
    # take it away from databases that legitimately got it from that revision.
    pass
