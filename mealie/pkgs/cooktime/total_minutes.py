"""Read a recipe's cook time as a number of minutes.

`RecipeModel.total_time` is free text, because that is what recipe sites emit and
what a person types: "30 minutes", "1 Hour 15 Minutes", "PT50M", "1 hr 5 min".
That is fine for display and useless for a question like "show me everything I
can cook on a Tuesday" — a string column cannot answer it, and neither can a
filter built on tags somebody has to remember to apply.

So the length is parsed once, on the way in, into `RecipeModel.total_minutes`.
This module is the only place that parsing happens.

Deliberately returns None rather than a guess. A cook time nobody can read is
not thirty minutes, and a filter that quietly treats it as thirty minutes is
worse than one that admits it does not know.
"""

import re
from datetime import timedelta

__all__ = ["total_minutes"]

# "1 hour 30 minutes", "45 min", "2hr", "90 m". Each unit is matched on its own so
# the parts can appear in any order and any combination.
_UNIT_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b",
    re.IGNORECASE,
)

_UNIT_MINUTES = {
    "d": 60 * 24,
    "day": 60 * 24,
    "days": 60 * 24,
    "h": 60,
    "hr": 60,
    "hrs": 60,
    "hour": 60,
    "hours": 60,
    "m": 1,
    "min": 1,
    "mins": 1,
    "minute": 1,
    "minutes": 1,
    # Seconds round into the minute below; a recipe measured in seconds is still a
    # recipe, and dropping it entirely would read as "no cook time".
    "s": 1 / 60,
    "sec": 1 / 60,
    "secs": 1 / 60,
    "second": 1 / 60,
    "seconds": 1 / 60,
}

# ISO 8601 durations, which is what schema.org recipes carry: PT50M, PT1H30M, P1DT2H.
_ISO_PATTERN = re.compile(
    r"^P(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$",
    re.IGNORECASE,
)


def total_minutes(value: str | int | float | timedelta | None) -> int | None:
    """Minutes as a whole number, or None when the value says nothing usable.

    Rounds to the nearest minute: the household's thresholds are whole minutes
    apart, so sub-minute precision would only ever be noise that makes a 45.4
    minute recipe sort differently from a 45 minute one.
    """
    if value is None:
        return None

    if isinstance(value, timedelta):
        return _round(value.total_seconds() / 60)

    if isinstance(value, bool):
        # bool is an int subclass, and True is not one minute.
        return None

    if isinstance(value, int | float):
        return _round(value) if value >= 0 else None

    text = str(value).strip()
    if not text:
        return None

    iso = _ISO_PATTERN.match(text)
    # `P` alone matches the pattern with every group empty; that is not a duration.
    if iso and any(iso.group(name) for name in ("days", "hours", "minutes", "seconds")):
        minutes = (
            float(iso.group("days") or 0) * 60 * 24
            + float(iso.group("hours") or 0) * 60
            + float(iso.group("minutes") or 0)
            + float(iso.group("seconds") or 0) / 60
        )
        return _round(minutes)

    matches = list(_UNIT_PATTERN.finditer(text))
    if matches:
        minutes = sum(float(m.group("value")) * _UNIT_MINUTES[m.group("unit").lower()] for m in matches)
        return _round(minutes)

    # A bare number is the one remaining form Mealie writes, and it means minutes.
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return _round(float(text))

    return None


def _round(minutes: float) -> int | None:
    if minutes < 0:
        return None
    return int(round(minutes))
