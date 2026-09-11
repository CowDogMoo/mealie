"""Turn whatever a person pastes -- a recipe URL, a bare host, a host with `www.` -- into the one
domain string the household source list is keyed on, and enumerate the parents that list should be
searched for.

The list is keyed on a registrable-looking domain rather than a full URL because the judgement it
records ("this site's recipes are worth trusting") is about the site, and every recipe URL on a
site shares the host. `www.` is stripped for the same reason: `www.budgetbytes.com` and
`budgetbytes.com` are one site in every sense a cook cares about.
"""

from urllib.parse import urlsplit

MIN_LABELS = 2
"""A lookup walks up the host's labels but never past this many, so a list entry can cover a whole
site (`nytimes.com` covers `cooking.nytimes.com`) without a stray entry for a bare TLD covering the
entire internet."""


def normalize_domain(value: str) -> str:
    """Return the lower-cased host of `value` without `www.`, port, path or credentials.

    Accepts a full URL or a bare host. Raises `ValueError` when no host can be found, so a caller
    that stores the result never stores an empty key.
    """

    raw = (value or "").strip()
    if not raw:
        raise ValueError("domain is required")

    # urlsplit only recognises a host when a scheme is present; a bare "example.com/x" would
    # otherwise parse entirely as a path
    if "://" not in raw:
        raw = f"//{raw}"

    host = (urlsplit(raw).hostname or "").strip().strip(".").lower()
    if host.startswith("www."):
        host = host[len("www.") :]

    if not host or any(not label for label in host.split(".")):
        raise ValueError(f"'{value}' does not contain a usable domain")

    return host


def candidate_domains(domain: str) -> list[str]:
    """The domain itself, then each parent down to `MIN_LABELS` labels, most specific first.

    `cooking.nytimes.com` -> `["cooking.nytimes.com", "nytimes.com"]`. A caller answers a lookup
    with the first candidate that has a list entry, so a specific entry beats a broader one.
    """

    labels = domain.split(".")
    return [".".join(labels[i:]) for i in range(0, max(1, len(labels) - MIN_LABELS + 1))]
