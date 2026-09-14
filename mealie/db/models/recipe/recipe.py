from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
import sqlalchemy.orm as orm
from pydantic import ConfigDict
from sqlalchemy import event
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.ext.orderinglist import ordering_list
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.orm.attributes import get_history
from sqlalchemy.orm.session import object_session

from mealie.db.models._model_utils.auto_init import auto_init
from mealie.db.models._model_utils.datetime import NaiveDateTime, get_utc_today
from mealie.db.models._model_utils.guid import GUID
from mealie.db.models.recipe.ingredient import RecipeIngredientModel
from mealie.pkgs.cooktime import total_minutes as parse_total_minutes

from .._model_base import BaseMixins, FilterableColumn, SqlAlchemyBase
from ..household.household_to_recipe import HouseholdToRecipe
from ..users.user_to_recipe import UserToRecipe
from .api_extras import ApiExtras, api_extras
from .assets import RecipeAsset
from .category import recipes_to_categories
from .comment import RecipeComment
from .instruction import RecipeInstruction
from .note import Note
from .nutrition import Nutrition
from .recipe_timeline import RecipeTimelineEvent
from .settings import RecipeSettings
from .shared import RecipeShareTokenModel
from .tag import recipes_to_tags
from .tool import recipes_to_tools

if TYPE_CHECKING:
    from ..group import Group, GroupMealPlan
    from ..household import Household, ShoppingListItemRecipeReference, ShoppingListRecipeReference
    from ..users import User
    from . import Category, Tag, Tool


class RecipeModel(SqlAlchemyBase, BaseMixins):
    __tablename__ = "recipes"
    __table_args__: tuple[sa.UniqueConstraint, ...] = (
        sa.UniqueConstraint("slug", "group_id", name="recipe_slug_group_id_key"),
    )

    id: FilterableColumn[GUID] = mapped_column(GUID, primary_key=True, default=GUID.generate)
    slug: FilterableColumn[str | None] = mapped_column(sa.String, index=True)

    # ID Relationships
    group_id: FilterableColumn[GUID] = mapped_column(GUID, sa.ForeignKey("groups.id"), nullable=False, index=True)
    group: Mapped["Group"] = orm.relationship("Group", back_populates="recipes", foreign_keys=[group_id])

    household_id: AssociationProxy[GUID] = association_proxy("user", "household_id")
    household: AssociationProxy["Household"] = association_proxy("user", "household")

    user_id: FilterableColumn[GUID | None] = mapped_column(GUID, sa.ForeignKey("users.id", use_alter=True), index=True)
    user: Mapped["User"] = orm.relationship("User", uselist=False, foreign_keys=[user_id])

    rating: FilterableColumn[float | None] = mapped_column(sa.Float, index=True, nullable=True)
    rated_by: Mapped[list["User"]] = orm.relationship(
        "User",
        secondary=UserToRecipe.__tablename__,
        back_populates="rated_recipes",
        overlaps="recipe,favorited_by,favorited_recipes",
    )
    favorited_by: Mapped[list["User"]] = orm.relationship(
        "User",
        secondary=UserToRecipe.__tablename__,
        primaryjoin="and_(RecipeModel.id==UserToRecipe.recipe_id, UserToRecipe.is_favorite==True)",
        viewonly=True,
        overlaps="recipe,rated_by,rated_recipes",
    )

    meal_entries: Mapped[list["GroupMealPlan"]] = orm.relationship(
        "GroupMealPlan", back_populates="recipe", cascade="all, delete-orphan"
    )

    # General Recipe Properties
    name: FilterableColumn[str] = mapped_column(sa.String, nullable=False)
    description: FilterableColumn[str | None] = mapped_column(sa.String)

    image: FilterableColumn[str | None] = mapped_column(sa.String)

    # Time Related Properties
    total_time: FilterableColumn[str | None] = mapped_column(sa.String)
    # `total_time` is free text because that is what recipe sites emit and what a
    # person types. A string cannot answer "what can I cook on a Tuesday", so the
    # length is parsed once on the way in and kept here. Derived, never accepted
    # from input -- see `_derive_total_minutes` below. Indexed because filtering
    # and sorting the browse grid by cook time is the whole point of it existing.
    total_minutes: FilterableColumn[int | None] = mapped_column(sa.Integer, index=True)
    prep_time: FilterableColumn[str | None] = mapped_column(sa.String)
    perform_time: FilterableColumn[str | None] = mapped_column(sa.String)
    cook_time: FilterableColumn[str | None] = mapped_column(sa.String)

    recipe_yield: FilterableColumn[str | None] = mapped_column(sa.String)
    recipe_yield_quantity: FilterableColumn[float] = mapped_column(sa.Float, index=True, default=0)
    recipe_servings: FilterableColumn[float] = mapped_column(sa.Float, index=True, default=0)

    assets: Mapped[list[RecipeAsset]] = orm.relationship("RecipeAsset", cascade="all, delete-orphan")
    nutrition: Mapped[Nutrition] = orm.relationship("Nutrition", uselist=False, cascade="all, delete-orphan")
    recipe_category: Mapped[list["Category"]] = orm.relationship(
        "Category", secondary=recipes_to_categories, back_populates="recipes"
    )
    tools: Mapped[list["Tool"]] = orm.relationship("Tool", secondary=recipes_to_tools, back_populates="recipes")

    recipe_ingredient: Mapped[list["RecipeIngredientModel"]] = orm.relationship(
        "RecipeIngredientModel",
        cascade="all, delete-orphan",
        order_by="RecipeIngredientModel.position",
        collection_class=ordering_list("position"),
        foreign_keys="RecipeIngredientModel.recipe_id",
    )
    referenced_ingredients: Mapped[list["RecipeIngredientModel"]] = orm.relationship(
        "RecipeIngredientModel",
        foreign_keys="RecipeIngredientModel.referenced_recipe_id",
        back_populates="referenced_recipe",
    )
    recipe_instructions: Mapped[list[RecipeInstruction]] = orm.relationship(
        "RecipeInstruction",
        cascade="all, delete-orphan",
        order_by="RecipeInstruction.position",
        collection_class=ordering_list("position"),
    )

    share_tokens: Mapped[list[RecipeShareTokenModel]] = orm.relationship(
        RecipeShareTokenModel, back_populates="recipe", cascade="all, delete, delete-orphan"
    )

    comments: Mapped[list[RecipeComment]] = orm.relationship(
        "RecipeComment", back_populates="recipe", cascade="all, delete, delete-orphan"
    )

    timeline_events: Mapped[list[RecipeTimelineEvent]] = orm.relationship(
        "RecipeTimelineEvent", back_populates="recipe", cascade="all, delete, delete-orphan"
    )

    # Mealie Specific
    settings: Mapped[list["RecipeSettings"]] = orm.relationship(
        "RecipeSettings", uselist=False, cascade="all, delete-orphan"
    )
    tags: Mapped[list["Tag"]] = orm.relationship("Tag", secondary=recipes_to_tags, back_populates="recipes")
    notes: Mapped[list[Note]] = orm.relationship("Note", cascade="all, delete-orphan")
    org_url: FilterableColumn[str | None] = mapped_column(sa.String)
    extras: Mapped[list[ApiExtras]] = orm.relationship("ApiExtras", cascade="all, delete-orphan")

    # Time Stamp Properties
    date_added: FilterableColumn[date | None] = mapped_column(sa.Date, default=get_utc_today)
    date_updated: FilterableColumn[datetime | None] = mapped_column(NaiveDateTime)

    last_made: FilterableColumn[datetime | None] = mapped_column(NaiveDateTime)
    made_by: Mapped[list["Household"]] = orm.relationship(
        "Household", secondary=HouseholdToRecipe.__tablename__, back_populates="made_recipes"
    )

    # Shopping List Refs
    shopping_list_refs: Mapped[list["ShoppingListRecipeReference"]] = orm.relationship(
        "ShoppingListRecipeReference",
        back_populates="recipe",
        cascade="all, delete-orphan",
    )
    shopping_list_item_refs: Mapped[list["ShoppingListItemRecipeReference"]] = orm.relationship(
        "ShoppingListItemRecipeReference",
        back_populates="recipe",
        cascade="all, delete-orphan",
    )

    # Automatically updated by sqlalchemy event, do not write to this manually
    name_normalized: FilterableColumn[str] = mapped_column(sa.String, nullable=False, index=True)
    description_normalized: FilterableColumn[str | None] = mapped_column(sa.String, index=True)
    model_config = ConfigDict(
        get_attr="slug",
        exclude={
            "assets",
            "notes",
            "nutrition",
            "recipe_ingredient",
            "recipe_instructions",
            "settings",
            "comments",
            "timeline_events",
        },
    )

    # Deprecated
    recipeCuisine: Mapped[str | None] = mapped_column(sa.String)
    is_ocr_recipe: Mapped[bool | None] = mapped_column(sa.Boolean, default=False)

    @validates("name")
    def validate_name(self, _, name):
        assert name != ""
        return name

    @api_extras
    @auto_init()
    def __init__(
        self,
        session,
        name: str | None = None,
        description: str | None = None,
        assets: list | None = None,
        notes: list[dict] | None = None,
        nutrition: dict | None = None,
        recipe_ingredient: list[dict] | None = None,
        recipe_instructions: list[dict] | None = None,
        settings: dict | None = None,
        **_,
    ) -> None:
        self.nutrition = Nutrition(**(nutrition or {}))

        if recipe_instructions is not None:
            self.recipe_instructions = [RecipeInstruction(**step, session=session) for step in recipe_instructions]

        if recipe_ingredient is not None:
            self.recipe_ingredient = [RecipeIngredientModel(**ingr, session=session) for ingr in recipe_ingredient]

        if assets:
            self.assets = [RecipeAsset(**a) for a in assets]

        self.settings = RecipeSettings(**(settings or {}))

        if notes:
            self.notes = [Note(**n) for n in notes]

        self.date_updated = datetime.now(UTC)

        # SQLAlchemy events do not seem to register things that are set during auto_init
        if name is not None:
            self.name_normalized = self.normalize(name)

        if description is not None:
            self.description_normalized = self.normalize(description)

        tableargs = [  # base set of indices
            sa.UniqueConstraint("slug", "group_id", name="recipe_slug_group_id_key"),
            sa.Index(
                "ix_recipes_name_normalized",
                "name_normalized",
                unique=False,
            ),
            sa.Index(
                "ix_recipes_description_normalized",
                "description_normalized",
                unique=False,
            ),
        ]

        if session.get_bind().name == "postgresql":
            tableargs.extend(
                [
                    sa.Index(
                        "ix_recipes_name_normalized_gin",
                        "name_normalized",
                        unique=False,
                        postgresql_using="gin",
                        postgresql_ops={
                            "name_normalized": "gin_trgm_ops",
                        },
                    ),
                    sa.Index(
                        "ix_recipes_description_normalized_gin",
                        "description_normalized",
                        unique=False,
                        postgresql_using="gin",
                        postgresql_ops={
                            "description_normalized": "gin_trgm_ops",
                        },
                    ),
                ]
            )
        # add indices
        self.__table_args__ = tuple(tableargs)


@event.listens_for(RecipeModel.name, "set")
def receive_name(target: RecipeModel, value: str, oldvalue, initiator):
    target.name_normalized = RecipeModel.normalize(value)


@event.listens_for(RecipeModel.description, "set")
def receive_description(target: RecipeModel, value: str, oldvalue, initiator):
    if value is not None:
        target.description_normalized = RecipeModel.normalize(value)
    else:
        target.description_normalized = None


@event.listens_for(RecipeModel.total_time, "set")
def receive_total_time(target: RecipeModel, value, oldvalue, initiator):
    """Keep the parsed length fresh in memory, the way `name_normalized` is kept.

    This is what lets code set `total_time` and read `total_minutes` back before
    the flush. It is not what makes the stored value trustworthy -- see
    `derive_total_minutes` below, which is the one that cannot be got around.
    """
    target.total_minutes = parse_total_minutes(value)


@event.listens_for(RecipeModel, "before_insert")
@event.listens_for(RecipeModel, "before_update")
def derive_total_minutes(mapper, connection, target: RecipeModel):
    """The stored length is derived from `total_time` at write time, always.

    `total_minutes` is on the recipe schema so the UI can read it, which means a
    client PUTting a whole recipe back sends a value for it -- and when the user
    has just edited the cook time, the value it sends is the old one. Deriving
    here, after everything else has been assigned, means input order and stale
    client values cannot decide what a filter later reads.
    """
    target.total_minutes = parse_total_minutes(target.total_time)


@event.listens_for(RecipeModel, "before_update")
def calculate_rating(mapper, connection, target: RecipeModel):
    session = object_session(target)
    if not (session and session.is_modified(target, "rating")):
        return

    history = get_history(target, "rating")
    old_value = history.deleted[0] if history.deleted else None
    new_value = history.added[0] if history.added else None
    if old_value == new_value:
        return

    target.rating = (
        session.query(sa.func.avg(UserToRecipe.rating))
        .filter(UserToRecipe.recipe_id == target.id, UserToRecipe.rating is not None, UserToRecipe.rating > 0)
        .scalar()
    )
