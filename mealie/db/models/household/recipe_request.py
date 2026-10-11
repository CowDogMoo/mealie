from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, String, Text, orm
from sqlalchemy.orm import Mapped, mapped_column

from .._filterable_column import FilterableColumn
from .._model_base import BaseMixins, SqlAlchemyBase
from .._model_utils.auto_init import auto_init
from .._model_utils.guid import GUID

if TYPE_CHECKING:
    from ..group import Group
    from .household import Household


class HouseholdRecipeRequestModel(SqlAlchemyBase, BaseMixins):
    """One person asking for a new recipe to be found and added to the household's recipe box.

    Not attached to any recipe: the "find me a new one" die on a card is the request to use when
    there is a recipe to be like, and it is a feedback event. This is the request with nothing to
    anchor it, so it needs a row of its own, and something outside Mealie -- the cluster runner --
    picks it up and reports back on it.

    The recipe that answered is kept as a snapshot (id, slug, name) rather than a foreign key, so
    deleting that recipe later never has to touch this table, and the request still says what it
    was answered with.
    """

    __tablename__ = "household_recipe_requests"

    id: Mapped[GUID] = mapped_column(GUID, primary_key=True, default=GUID.generate)

    group: Mapped[Optional["Group"]] = orm.relationship("Group", back_populates="recipe_requests", single_parent=True)
    group_id: Mapped[GUID | None] = mapped_column(GUID, ForeignKey("groups.id"), index=True)
    household: Mapped[Optional["Household"]] = orm.relationship(
        "Household", back_populates="recipe_requests", single_parent=True
    )
    household_id: Mapped[GUID | None] = mapped_column(GUID, ForeignKey("households.id"), index=True)

    # who asked, by id and by the name they had at the time; no foreign key, so removing a user
    # leaves their old requests readable instead of blocking the delete
    requested_by: Mapped[GUID | None] = mapped_column(GUID, nullable=True)
    requested_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: FilterableColumn[str] = mapped_column(String(16), nullable=False, index=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)

    recipe_id: Mapped[GUID | None] = mapped_column(GUID, nullable=True)
    recipe_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipe_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    @auto_init()
    def __init__(self, **_) -> None: ...
