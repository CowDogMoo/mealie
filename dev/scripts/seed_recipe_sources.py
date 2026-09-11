"""Load a tiered recipe-site list from markdown into a household's Mealie recipe source list.

The household's list in Mealie is the source of truth; this script exists to fill a fresh
household from a markdown file of the shape the weekday-dinner-recipes skill used to ship: three
tables under "Tier 1", "Tier 2" and "Do NOT use" headings, each row starting with a site name and
a URL pattern (or, for the lower tiers, a site name that `HOSTS_BY_NAME` knows). One entry per
site is posted through `/api/households/recipe-sources`. To copy a list between instances, take
the JSON from `GET /api/households/recipe-sources` instead.

Tier headings map to statuses:

    "Tier 1"     -> known-good
    "Tier 2"     -> caution
    "Do NOT use" -> blocked

A site already on the list is left exactly as it is: this script seeds, it never overrules a
judgement the household has since made by hand or through a vote.

Usage:

    uv run python dev/scripts/seed_recipe_sources.py --dry-run path/to/sources.md
    MEALIE_URL=https://mealie.example MEALIE_TOKEN=... \\
        uv run python dev/scripts/seed_recipe_sources.py path/to/sources.md
"""

# ruff: noqa: T201 -- a command-line tool; its printed report is the interface
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

TIER_STATUS: dict[str, str] = {
    "tier 1": "known-good",
    "tier 2": "caution",
    "do not use": "blocked",
}
"""Heading prefix (lower-cased, punctuation dropped) -> status. A heading that matches none of
these ends the current tier, so the "Fetch quirks" section contributes nothing."""

HOSTS_BY_NAME: dict[str, str] = {
    "nyt cooking": "cooking.nytimes.com",
    "bon appétit": "bonappetit.com",
    "smitten kitchen": "smittenkitchen.com",
    "serious eats": "seriouseats.com",
    "allrecipes": "allrecipes.com",
    "food network": "foodnetwork.com",
    "pinterest": "pinterest.com",
}
"""Tier 2 and the blocked tier list sites by name without a URL pattern; these are their hosts.
A name that is neither here nor accompanied by a URL is reported and skipped, never guessed."""

SKIPPED_ROWS = {"tiktok / instagram", "generic listicle/roundup pages"}
"""Rows that are not one site: the skill's blocked tier lists them, but there is no domain to key
an entry on. Reported as skipped so the dry run shows the whole table was read."""


@dataclass(frozen=True)
class SeedEntry:
    domain: str
    status: str
    note: str


def normalize_heading(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split("  ")[0].strip()


def status_for_heading(text: str) -> str | None:
    heading = normalize_heading(text)
    for prefix, status in TIER_STATUS.items():
        if heading.startswith(prefix):
            return status
    return None


def domain_from_pattern(pattern: str) -> str | None:
    """`onceuponachef.com/recipes/<slug>.html` -> `onceuponachef.com`."""

    candidate = pattern.strip().strip("`")
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = f"//{candidate}"
    host = (urllib.parse.urlsplit(candidate).hostname or "").strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_sources(markdown: str) -> tuple[list[SeedEntry], list[str]]:
    """Every seedable site in `markdown`, plus a human-readable line per row that was skipped."""

    entries: list[SeedEntry] = []
    skipped: list[str] = []
    status: str | None = None
    header: list[str] | None = None

    for line in markdown.splitlines():
        if line.startswith("#"):
            status = status_for_heading(line.lstrip("#"))
            header = None
            continue

        if status is None or not line.lstrip().startswith("|"):
            continue

        cells = split_row(line)
        if header is None:
            header = [cell.lower() for cell in cells]
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue  # the |---|---| separator

        row = dict(zip(header, cells, strict=False))
        name = row.get("site", "")
        if name.lower() in SKIPPED_ROWS:
            skipped.append(f"{status}: '{name}' is not one site")
            continue

        domain = domain_from_pattern(row.get("url pattern", "")) or HOSTS_BY_NAME.get(name.lower())
        if not domain:
            skipped.append(f"{status}: no domain known for '{name}'")
            continue

        detail = row.get("notes") or row.get("caveat") or row.get("why") or ""
        note = f"{name}. {detail}".strip().strip(".") if detail else name
        entries.append(SeedEntry(domain=domain, status=status, note=note))

    return entries, skipped


class MealieClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers=self.headers)
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - operator-supplied base URL
            return json.loads(response.read() or b"{}")

    def existing_domains(self) -> set[str]:
        page = self._request("GET", "/api/households/recipe-sources", params={"perPage": -1})
        return {item["domain"] for item in page.get("items", [])}

    def create(self, entry: SeedEntry) -> dict:
        return self._request(
            "POST",
            "/api/households/recipe-sources",
            body={"domain": entry.domain, "status": entry.status, "note": entry.note},
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sources", type=Path, help="path to a tiered markdown list (Tier 1 / Tier 2 / Do NOT use)")
    parser.add_argument("--dry-run", action="store_true", help="parse and report; touch nothing")
    parser.add_argument("--url", default=os.environ.get("MEALIE_URL"), help="Mealie base URL (or MEALIE_URL)")
    parser.add_argument("--token", default=os.environ.get("MEALIE_TOKEN"), help="API token (or MEALIE_TOKEN)")
    args = parser.parse_args(argv)

    entries, skipped = parse_sources(args.sources.read_text())
    if not entries:
        print("no seedable entries found", file=sys.stderr)
        return 1

    by_status: dict[str, int] = {}
    for entry in entries:
        by_status[entry.status] = by_status.get(entry.status, 0) + 1
    missing_statuses = set(TIER_STATUS.values()) - set(by_status)
    if missing_statuses:
        print(f"no entries parsed for: {', '.join(sorted(missing_statuses))}", file=sys.stderr)
        return 1

    for entry in entries:
        print(f"{entry.status:<11} {entry.domain:<28} {entry.note}")
    for line in skipped:
        print(f"skipped     {line}")

    if args.dry_run:
        print(
            f"SEED DRY RUN OK: {len(entries)} entries ({', '.join(f'{k}={v}' for k, v in sorted(by_status.items()))})"
        )
        return 0

    if not args.url or not args.token:
        print("--url/MEALIE_URL and --token/MEALIE_TOKEN are required outside --dry-run", file=sys.stderr)
        return 2

    client = MealieClient(args.url, args.token)
    existing = client.existing_domains()
    created = 0
    for entry in entries:
        if entry.domain in existing:
            print(f"kept        {entry.domain} (already listed)")
            continue
        try:
            client.create(entry)
        except urllib.error.HTTPError as e:
            print(
                f"failed      {entry.domain}: HTTP {e.code} {e.read().decode(errors='replace')[:200]}", file=sys.stderr
            )
            return 1
        created += 1
        print(f"created     {entry.domain} as {entry.status}")

    print(f"SEED OK: {created} created, {len(entries) - created} already listed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
