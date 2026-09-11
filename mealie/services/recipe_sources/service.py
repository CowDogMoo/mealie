"""Answer "what does this household think of the site behind this URL?" and apply the one
automatic transition the list has: a `bad-source` vote demotes a site to caution.
"""

from uuid import UUID

from mealie.repos.repository_factory import AllRepositories
from mealie.schema.household.recipe_source import (
    RecipeSourceLookupOut,
    RecipeSourceOut,
    RecipeSourceSave,
    RecipeSourceStatus,
)
from mealie.services._base_service import BaseService

from .domain import candidate_domains, normalize_domain


class RecipeSourceBlockedError(Exception):
    """Raised by the URL importer when the household has blocked the site.

    Carries the domain and the list entry so the importer can say *which* rule refused the
    import and quote the note the household left on it.
    """

    def __init__(self, domain: str, source: RecipeSourceOut) -> None:
        super().__init__(f"imports from {domain} are blocked")
        self.domain = domain
        self.source = source


class RecipeSourceService(BaseService):
    """Reads and the single write rule, over a household-scoped `recipe_sources` repository.

    `repos` must be household-scoped: the repository's own filters are what keep one household's
    list from answering another household's lookup, so a group-wide `AllRepositories` here would
    quietly widen the boundary.
    """

    def __init__(self, repos: AllRepositories) -> None:
        super().__init__()
        self.repos = repos

    def lookup(self, url_or_domain: str) -> RecipeSourceLookupOut:
        """The most specific list entry covering the host of `url_or_domain`, or none.

        Raises `ValueError` when no host can be read from the input; the caller decides whether
        that is a 422 or simply "nothing to check".
        """

        domain = normalize_domain(url_or_domain)
        for candidate in candidate_domains(domain):
            if source := self.repos.recipe_sources.get_one(candidate, key="domain"):
                return RecipeSourceLookupOut(domain=domain, status=source.status, source=source)

        return RecipeSourceLookupOut(domain=domain)

    def assert_not_blocked(self, url: str) -> RecipeSourceLookupOut | None:
        """Look `url` up and raise `RecipeSourceBlockedError` if the household has blocked it.

        A URL with no readable host answers `None` rather than raising: the scraper is about to
        reject it on its own terms, and a source-list error would misdescribe that failure.
        """

        try:
            found = self.lookup(url)
        except ValueError:
            return None

        if found.source is not None and found.status == RecipeSourceStatus.blocked:
            raise RecipeSourceBlockedError(found.domain, found.source)

        return found

    def demote(self, url_or_domain: str, note: str) -> RecipeSourceOut | None:
        """Move a site to caution because a `bad-source` vote was cast against it.

        Only `known-good` and unlisted sites move: a site already on caution stays there, and a
        blocked site is never softened by a vote. The note is appended rather than replaced, so a
        person reading the entry later sees every vote that put it there, not just the latest.

        Returns the entry after the change, or `None` when the input had no usable host.
        """

        try:
            domain = normalize_domain(url_or_domain)
        except ValueError:
            return None

        existing = self.repos.recipe_sources.get_one(domain, key="domain")
        if existing is None:
            group_id, household_id = self.repos.group_id, self.repos.household_id
            if not isinstance(group_id, UUID) or not isinstance(household_id, UUID):
                raise ValueError("demote needs a household-scoped repository")

            return self.repos.recipe_sources.create(
                RecipeSourceSave(
                    domain=domain,
                    status=RecipeSourceStatus.caution,
                    note=note,
                    group_id=group_id,
                    household_id=household_id,
                )
            )

        if existing.status != RecipeSourceStatus.known_good:
            return existing

        existing.status = RecipeSourceStatus.caution
        existing.note = f"{existing.note}\n{note}" if existing.note else note
        return self.repos.recipe_sources.update(existing.id, existing)
