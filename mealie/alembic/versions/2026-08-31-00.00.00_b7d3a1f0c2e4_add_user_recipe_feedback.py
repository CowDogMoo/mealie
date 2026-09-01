"""add user recipe feedback

Revision ID: b7d3a1f0c2e4
Revises: 2187537c52b8
Create Date: 2026-08-31 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

import mealie.db.migration_types

# revision identifiers, used by Alembic.
revision = "b7d3a1f0c2e4"
down_revision: str | None = "2187537c52b8"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade():
    op.create_table(
        "user_recipe_feedback",
        sa.Column("id", mealie.db.migration_types.GUID(), nullable=False),
        sa.Column("user_id", mealie.db.migration_types.GUID(), nullable=False),
        sa.Column("recipe_id", mealie.db.migration_types.GUID(), nullable=False),
        sa.Column("vote", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.String(length=48), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("advisory", sa.Boolean(), nullable=False),
        sa.Column("created_at", mealie.db.migration_types.NaiveDateTime(), nullable=True),
        sa.Column("update_at", mealie.db.migration_types.NaiveDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["recipe_id"],
            ["recipes.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        # a down vote must say why -- and '' is not a why. A reason stays optional for up and neutral.
        sa.CheckConstraint(
            "vote <> 'down' OR (reason IS NOT NULL AND reason <> '')",
            name="ck_user_recipe_feedback_down_has_reason",
        ),
    )
    with op.batch_alter_table("user_recipe_feedback", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_user_recipe_feedback_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_user_recipe_feedback_recipe_id"), ["recipe_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_user_recipe_feedback_user_id"), ["user_id"], unique=False)
        batch_op.create_index("ix_user_recipe_feedback_recipe_id_user_id", ["recipe_id", "user_id"], unique=False)
        batch_op.create_index("ix_user_recipe_feedback_user_id_created_at", ["user_id", "created_at"], unique=False)


def downgrade():
    with op.batch_alter_table("user_recipe_feedback", schema=None) as batch_op:
        batch_op.drop_index("ix_user_recipe_feedback_user_id_created_at")
        batch_op.drop_index("ix_user_recipe_feedback_recipe_id_user_id")
        batch_op.drop_index(batch_op.f("ix_user_recipe_feedback_user_id"))
        batch_op.drop_index(batch_op.f("ix_user_recipe_feedback_recipe_id"))
        batch_op.drop_index(batch_op.f("ix_user_recipe_feedback_created_at"))

    op.drop_table("user_recipe_feedback")
