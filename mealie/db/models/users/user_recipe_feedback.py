from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Index, String, Text
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .._model_base import BaseMixins, SqlAlchemyBase
from .._model_utils.auto_init import auto_init
from .._model_utils.guid import GUID

if TYPE_CHECKING:
    from ..recipe import RecipeModel


class UserRecipeFeedback(SqlAlchemyBase, BaseMixins):
    """An append-only log of feedback events: many rows per (user, recipe), never overwritten.

    Deliberately not columns on `users_to_recipes`, which holds one mutable row per pair. The
    confidence rule these rows feed counts how often a reason has been said, so repeats have to
    survive. This table also stays out of the aggregate-rating machinery `users_to_recipes`
    drives through its `after_insert/update/delete` listeners.
    """

    __tablename__ = "user_recipe_feedback"
    __table_args__ = (
        # The "a down vote carries a reason" rule is enforced by UserFeedbackIn on the way in, but
        # UserFeedbackOut inherits those validators, so a row that broke it -- hand-written SQL, a
        # partial restore, a future direct insert -- would raise while being *read* and fail the
        # whole household feed rather than one row. Stating it here makes the invariant true where
        # the data lives, so that read-time failure is unreachable through any supported path.
        # Deliberately narrow: a reason stays optional for `up` and `neutral`. The empty
        # string is excluded explicitly -- `IS NOT NULL` alone would admit `''`, which is
        # falsy to the same Pydantic validator, so the row would still fail on read and the
        # constraint would not have closed the hole it exists for.
        CheckConstraint(
            "vote <> 'down' OR (reason IS NOT NULL AND reason <> '')",
            name="ck_user_recipe_feedback_down_has_reason",
        ),
        Index("ix_user_recipe_feedback_recipe_id_user_id", "recipe_id", "user_id"),
        Index("ix_user_recipe_feedback_user_id_created_at", "user_id", "created_at"),
    )
    id: Mapped[GUID] = mapped_column(GUID, primary_key=True, default=GUID.generate)

    user_id = Column(GUID, ForeignKey("users.id"), index=True, nullable=False)
    recipe: Mapped["RecipeModel"] = relationship("RecipeModel")
    recipe_id = Column(GUID, ForeignKey("recipes.id"), index=True, nullable=False)
    group_id: AssociationProxy[GUID] = association_proxy("recipe", "group_id")
    household_id: AssociationProxy[GUID] = association_proxy("recipe", "household_id")

    vote = Column(String(8), nullable=False)
    reason = Column(String(48), nullable=True)
    scope = Column(String(16), nullable=False, default="recipe")
    target = Column(String(255), nullable=True)
    note = Column(Text, nullable=True)
    advisory = Column(Boolean, nullable=False)

    @auto_init()
    def __init__(self, **_) -> None:
        pass
