from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, orm
from sqlalchemy.orm import Mapped, mapped_column

from .._filterable_column import FilterableColumn
from .._model_base import BaseMixins, SqlAlchemyBase
from .._model_utils.auto_init import auto_init
from .._model_utils.guid import GUID

if TYPE_CHECKING:
    from ..group import Group
    from .household import Household


class HouseholdRecipeSourceModel(SqlAlchemyBase, BaseMixins):
    """One household's judgement about one recipe site.

    Keyed on a normalized domain (see `mealie.services.recipe_sources.domain`) rather than a URL,
    because the judgement is about the site. One row per (household, domain): the status is
    current state, not a log, so a change overwrites in place. The history of *why* a site was
    demoted lives in `user_recipe_feedback`, which is what writes the demotion.
    """

    __tablename__ = "household_recipe_sources"
    __table_args__ = (UniqueConstraint("household_id", "domain", name="household_recipe_sources_household_domain_key"),)

    id: Mapped[GUID] = mapped_column(GUID, primary_key=True, default=GUID.generate)

    group: Mapped[Optional["Group"]] = orm.relationship("Group", back_populates="recipe_sources", single_parent=True)
    group_id: Mapped[GUID | None] = mapped_column(GUID, ForeignKey("groups.id"), index=True)
    household: Mapped[Optional["Household"]] = orm.relationship(
        "Household", back_populates="recipe_sources", single_parent=True
    )
    household_id: Mapped[GUID | None] = mapped_column(GUID, ForeignKey("households.id"), index=True)

    # FilterableColumn rather than Mapped: the list page orders by domain and a caller may
    # filter by status, and the pagination layer only accepts columns declared this way
    domain: FilterableColumn[str] = mapped_column(String(255), nullable=False, index=True)
    status: FilterableColumn[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    @auto_init()
    def __init__(self, **_) -> None: ...
