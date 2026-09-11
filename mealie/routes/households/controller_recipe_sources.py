"""A household's recipe source list: which sites it trusts, which it is wary of, which it refuses.

Any household member may read and write the list. The household is already the trust boundary
for meal plans, shopping lists and cookbooks, and `controller_household_feedback.py` makes the
same argument for votes; a site judgement is no more private than either. The list is keyed on a
normalized domain, and every entry belongs to the caller's household through the repository's own
group and household filters, never through anything the request can name.
"""

from functools import cached_property

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import UUID4

from mealie.routes._base.base_controllers import BaseUserController
from mealie.routes._base.controller import controller
from mealie.routes._base.mixins import HttpRepo
from mealie.schema import mapper
from mealie.schema.household.recipe_source import (
    RecipeSourceCreate,
    RecipeSourceLookupOut,
    RecipeSourceOut,
    RecipeSourcePagination,
    RecipeSourceSave,
)
from mealie.schema.response.pagination import PaginationQuery
from mealie.schema.response.responses import ErrorResponse
from mealie.services.recipe_sources.service import RecipeSourceService

router = APIRouter(prefix="/households/recipe-sources", tags=["Households: Recipe Sources"])


@controller(router)
class HouseholdRecipeSourceController(BaseUserController):
    @cached_property
    def repo(self):
        return self.repos.recipe_sources

    @cached_property
    def service(self) -> RecipeSourceService:
        return RecipeSourceService(self.repos)

    @property
    def mixins(self) -> HttpRepo:
        return HttpRepo[RecipeSourceSave, RecipeSourceOut, RecipeSourceCreate](self.repo, self.logger)

    def assert_domain_free(self, domain: str, except_id: UUID4 | None = None) -> None:
        """409 when another entry in this household already claims `domain`.

        Checked ahead of the write rather than left to the unique constraint so the caller gets a
        status that says "already listed" instead of the mixin's generic 400.
        """

        existing = self.repo.get_one(domain, key="domain")
        if existing is not None and existing.id != except_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=ErrorResponse.respond(message=f"{domain} is already on this household's recipe source list"),
            )

    @router.get("", response_model=RecipeSourcePagination)
    def get_all(self, q: PaginationQuery = Depends(PaginationQuery)):
        response = self.repo.page_all(pagination=q, override=RecipeSourceOut)
        response.set_pagination_guides(router.url_path_for("get_all"), q.model_dump())
        return response

    @router.get("/lookup", response_model=RecipeSourceLookupOut)
    def lookup(
        self, url: str = Query(..., description="A recipe URL or bare domain to check against the list")
    ) -> RecipeSourceLookupOut:
        """What this household thinks of the site behind `url`.

        The most specific entry wins: an entry for `nytimes.com` answers for `cooking.nytimes.com`
        unless the household has listed the subdomain itself. `status` and `source` are both null
        when nothing on the list covers the site.
        """

        try:
            return self.service.lookup(url)
        except ValueError as e:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=ErrorResponse.respond(message=str(e))
            ) from e

    @router.post("", response_model=RecipeSourceOut, status_code=201)
    def create_one(self, data: RecipeSourceCreate):
        self.assert_domain_free(data.domain)
        save = mapper.cast(data, RecipeSourceSave, group_id=self.group_id, household_id=self.household_id)
        return self.mixins.create_one(save)

    @router.get("/{item_id}", response_model=RecipeSourceOut)
    def get_one(self, item_id: UUID4):
        return self.mixins.get_one(item_id)

    @router.put("/{item_id}", response_model=RecipeSourceOut)
    def update_one(self, item_id: UUID4, data: RecipeSourceCreate):
        self.assert_domain_free(data.domain, except_id=item_id)
        return self.mixins.update_one(data, item_id)

    @router.delete("/{item_id}", response_model=RecipeSourceOut)
    def delete_one(self, item_id: UUID4):
        return self.mixins.delete_one(item_id)  # type: ignore
