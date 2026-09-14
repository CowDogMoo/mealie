"""add recipe total_minutes

Revision ID: e5b9c3d7a248
Revises: d4a8b2c6e9f1
Create Date: 2026-09-13 00:00:00.000000

`recipes.total_time` is free text -- "30 minutes", "1 Hour 15 Minutes", "PT50M" --
because that is what recipe sites emit and what a person types. A string column
cannot answer "show me everything I can cook on a Tuesday", so the length is
parsed once on write into `total_minutes` and the browse grid filters on that.

The backfill has to run in Python: no SQL dialect can read "1 Hour 15 Minutes".
It parses each distinct `total_time` once and writes one UPDATE per distinct
value rather than one per recipe, so a library with thousands of recipes and a
few dozen distinct times costs a few dozen statements.

A row whose time cannot be parsed keeps NULL. That is the honest answer -- a
cook time nobody can read is not thirty minutes -- and it is what the filter
treats as "no cook time" rather than silently including it in a weeknight query.
"""

import sqlalchemy as sa
from alembic import op

from mealie.pkgs.cooktime import total_minutes

# revision identifiers, used by Alembic.
revision = "e5b9c3d7a248"
down_revision: str | None = "d4a8b2c6e9f1"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


recipes_table = sa.table(
    "recipes",
    sa.column("id", sa.String),
    sa.column("total_time", sa.String),
    sa.column("total_minutes", sa.Integer),
)


def upgrade():
    op.add_column("recipes", sa.Column("total_minutes", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_recipes_total_minutes"), "recipes", ["total_minutes"], unique=False)

    connection = op.get_bind()
    distinct_times = connection.execute(
        sa.select(recipes_table.c.total_time).where(recipes_table.c.total_time.is_not(None)).distinct()
    ).scalars()

    for raw_time in distinct_times:
        minutes = total_minutes(raw_time)
        if minutes is None:
            continue
        connection.execute(
            recipes_table.update().where(recipes_table.c.total_time == raw_time).values(total_minutes=minutes)
        )


def downgrade():
    op.drop_index(op.f("ix_recipes_total_minutes"), table_name="recipes")
    op.drop_column("recipes", "total_minutes")
