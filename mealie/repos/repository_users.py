import random
import shutil
from collections.abc import Sequence
from datetime import datetime

from pydantic import UUID4
from sqlalchemy import select

from mealie.assets import users as users_assets
from mealie.core.config import get_app_settings
from mealie.db.models.users.user_recipe_feedback import UserRecipeFeedback
from mealie.db.models.users.user_to_recipe import UserToRecipe
from mealie.schema.user.user import PrivateUser, UserRatingOut
from mealie.schema.user.user_feedback import UserFeedbackOut

from ..db.models.users import User
from .repository_generic import GroupRepositoryGeneric

settings = get_app_settings()


class RepositoryUsers(GroupRepositoryGeneric[PrivateUser, User]):
    def update_password(self, id, password: str):
        entry = self._query_one(match_value=id)
        if settings.IS_DEMO:
            user_to_update = self.schema.model_validate(entry)
            if user_to_update.is_default_user:
                # do not update the default user in demo mode
                return user_to_update

        entry.update_password(password)
        self.session.commit()

        return self.schema.model_validate(entry)

    def create(self, user: PrivateUser | dict):  # type: ignore
        new_user = super().create(user)

        # Select Random Image
        all_images = [
            users_assets.img_random_1,
            users_assets.img_random_2,
            users_assets.img_random_3,
        ]
        random_image = random.choice(all_images)
        shutil.copy(random_image, new_user.directory() / "profile.webp")

        return new_user

    def update(self, match_value: str | int | UUID4, new_data: dict | PrivateUser) -> PrivateUser:
        if settings.IS_DEMO:
            user_to_update = self.get_one(match_value)
            if user_to_update and user_to_update.is_default_user:
                # do not update the default user in demo mode
                return user_to_update

        return super().update(match_value, new_data)

    def delete(self, value: str | UUID4, match_key: str | None = None) -> User:
        if settings.IS_DEMO:
            user_to_delete = self.get_one(value, match_key)
            if user_to_delete and user_to_delete.is_default_user:
                # do not update the default user in demo mode
                return user_to_delete

        entry = super().delete(value, match_key)
        # Delete the user's directory
        shutil.rmtree(PrivateUser.get_directory(value))
        return entry

    def get_by_username(self, username: str) -> PrivateUser | None:
        stmt = select(User).filter(User.username == username)
        dbuser = self.session.execute(stmt).scalars().one_or_none()
        return None if dbuser is None else self.schema.model_validate(dbuser)

    def get_locked_users(self) -> list[PrivateUser]:
        stmt = select(User).filter(User.locked_at != None)  # noqa E711
        results = self.session.execute(stmt).scalars().all()
        return [self.schema.model_validate(x) for x in results]


class RepositoryUserRatings(GroupRepositoryGeneric[UserRatingOut, UserToRecipe]):
    # Since users can post events on recipes that belong to other households,
    # this is a group repository, rather than a household repository.

    def get_by_user(self, user_id: UUID4, favorites_only=False) -> list[UserRatingOut]:
        stmt = select(UserToRecipe).filter(UserToRecipe.user_id == user_id)
        if favorites_only:
            stmt = stmt.filter(UserToRecipe.is_favorite)

        results = self.session.execute(stmt).scalars().all()
        return [self.schema.model_validate(x) for x in results]

    def get_by_recipe(self, recipe_id: UUID4, favorites_only=False) -> list[UserRatingOut]:
        stmt = select(UserToRecipe).filter(UserToRecipe.recipe_id == recipe_id)
        if favorites_only:
            stmt = stmt.filter(UserToRecipe.is_favorite)

        results = self.session.execute(stmt).scalars().all()
        return [self.schema.model_validate(x) for x in results]

    def get_by_user_and_recipe(self, user_id: UUID4, recipe_id: UUID4) -> UserRatingOut | None:
        stmt = select(UserToRecipe).filter(UserToRecipe.user_id == user_id, UserToRecipe.recipe_id == recipe_id)
        result = self.session.execute(stmt).scalars().one_or_none()
        return None if result is None else self.schema.model_validate(result)

    def get_by_users(self, user_ids: Sequence[UUID4], favorites_only: bool = False) -> list[UserRatingOut]:
        """Ratings held by any of `user_ids`, restricted to this repository's group.

        `get_by_user` above is upstream's and is deliberately left alone: it backs
        `/api/users/{id}/ratings`, which is self-only, so a user reading their own rows across
        groups is not a disclosure. This method backs a *household* read, where the caller is
        someone else, and there the group filter matters. A user who was moved between groups
        keeps their old `users_to_recipes` rows, which point at the old group's recipes; without
        the filter those rows follow them into the new household's view, which is precisely the
        cross-group reach D4 rules out.
        """

        ids = list(user_ids)
        if not ids:
            return []

        stmt = select(UserToRecipe).filter(UserToRecipe.user_id.in_(ids)).filter_by(**self._filter_builder())
        if favorites_only:
            stmt = stmt.filter(UserToRecipe.is_favorite)

        results = self.session.execute(stmt).scalars().all()
        return [self.schema.model_validate(x) for x in results]


class RepositoryUserFeedback(GroupRepositoryGeneric[UserFeedbackOut, UserRecipeFeedback]):
    # Since users can post events on recipes that belong to other households,
    # this is a group repository, rather than a household repository.
    #
    # Both reads below carry the repository's group filter, which the ratings reads above do
    # not. A user id says nothing about which group's recipes its events hang off, so without
    # the filter a caller scoped to one group could read events on another group's recipes by
    # passing the right id. The filter costs an EXISTS against `recipes`; an unscoped
    # repository (group_id None, i.e. admin) still sees everything, as it does elsewhere.

    def get_by_user(self, user_id: UUID4) -> list[UserFeedbackOut]:
        """Every event one user has cast, oldest first."""

        stmt = (
            select(UserRecipeFeedback)
            .filter(UserRecipeFeedback.user_id == user_id)
            .filter_by(**self._filter_builder())
            .order_by(UserRecipeFeedback.created_at, UserRecipeFeedback.id)
        )

        results = self.session.execute(stmt).scalars().all()
        return [self.schema.model_validate(x) for x in results]

    def get_by_users(
        self,
        user_ids: Sequence[UUID4],
        recipe_id: UUID4 | None = None,
        since: datetime | None = None,
        vote: str | None = None,
    ) -> list[UserFeedbackOut]:
        """Every event cast by any of `user_ids`, oldest first.

        Ordering is part of the contract, not a nicety: consumers count how often a reason has
        been repeated over time, so the sequence has to be the one the events happened in.

        Each filter is optional; None means "no filter", and only None does, so `vote=""`
        narrows to nothing rather than quietly matching every row. Asking about nobody
        (`user_ids=[]`) likewise returns nothing rather than the whole table.
        """

        ids = list(user_ids)
        if not ids:
            return []

        stmt = select(UserRecipeFeedback).filter(UserRecipeFeedback.user_id.in_(ids))

        if recipe_id is not None:
            stmt = stmt.filter(UserRecipeFeedback.recipe_id == recipe_id)

        if since is not None:
            # the column is naive UTC; NaiveDateTime normalizes an aware bound on the way in
            stmt = stmt.filter(UserRecipeFeedback.created_at >= since)

        if vote is not None:
            stmt = stmt.filter(UserRecipeFeedback.vote == vote)

        stmt = stmt.filter_by(**self._filter_builder()).order_by(UserRecipeFeedback.created_at, UserRecipeFeedback.id)

        results = self.session.execute(stmt).scalars().all()
        return [self.schema.model_validate(x) for x in results]
