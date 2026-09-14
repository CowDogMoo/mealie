#!/usr/bin/env python
"""The cook-time column holds up on a library bigger than this household's.

Two properties, both measured rather than asserted in a comment:

1. The backfill costs one UPDATE per *distinct* cook time, not one per recipe.
   A library with 600 recipes and 12 distinct times must issue 12 updates. The
   statements are counted through a SQLAlchemy engine event, so this measures
   what the migration actually executed rather than what it looks like it does.

2. The filter reads the index instead of scanning the table. `EXPLAIN QUERY
   PLAN` has to name `ix_recipes_total_minutes`; a plan that says SCAN means the
   index was created and then never used, which is the failure that only shows
   up once somebody has a few thousand recipes.

Run: uv run python dev/scripts/check_total_minutes_scale.py
"""

# ruff: noqa: T201 -- a command-line check; its printed report is the interface

import os
import sqlite3
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRATCH = Path("tests/.temp") / f"total-minutes-scale-{uuid.uuid4().hex[:8]}"
(REPO / SCRATCH).mkdir(parents=True, exist_ok=True)

# Settings are cached on first read, so the environment has to be right before
# anything under `mealie` is imported.
os.environ["TESTING"] = "true"
os.environ["PRODUCTION"] = "false"
os.environ["DATA_DIR"] = str(SCRATCH)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

PREVIOUS_REVISION = "d4a8b2c6e9f1"
RECIPE_COUNT = 600
DISTINCT_TIMES = [
    "10 minutes",
    "20 minutes",
    "25 minutes",
    "30 minutes",
    "35 minutes",
    "40 minutes",
    "45 minutes",
    "50 min",
    "1 hour",
    "1 Hour 15 Minutes",
    "PT2H5M",
    "some evening",  # unreadable, so it is never updated
]
READABLE_TIMES = [t for t in DISTINCT_TIMES if t != "some evening"]

DB = REPO / SCRATCH / "mealie.db"

recipe_updates: list[str] = []


@event.listens_for(Engine, "before_cursor_execute")
def count_statements(_conn, _cursor, statement, _params, _context, _executemany):
    normalized = " ".join(statement.split()).upper()
    if normalized.startswith("UPDATE RECIPES"):
        recipe_updates.append(normalized)


def fail(message: str) -> None:
    print(f"SCALE-FAILED: {message}")
    sys.exit(1)


def alembic_config() -> Config:
    config = Config(str(REPO / "mealie/alembic/alembic.ini"))
    config.set_main_option("script_location", str(REPO / "mealie/alembic"))
    return config


def main() -> None:
    config = alembic_config()

    command.upgrade(config, PREVIOUS_REVISION)

    group_id, user_id = uuid.uuid4().hex, uuid.uuid4().hex
    with sqlite3.connect(DB) as conn:
        for index in range(RECIPE_COUNT):
            name = f"Scale probe {index}"
            conn.execute(
                "INSERT INTO recipes "
                "(id, slug, name, name_normalized, group_id, user_id, total_time, "
                " recipe_yield_quantity, recipe_servings) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
                (
                    uuid.uuid4().hex,
                    f"scale-{index}",
                    name,
                    name.lower(),
                    group_id,
                    user_id,
                    DISTINCT_TIMES[index % len(DISTINCT_TIMES)],
                ),
            )

    recipe_updates.clear()
    command.upgrade(config, "head")

    # 1. One UPDATE per distinct readable time, not per recipe.
    if len(recipe_updates) > len(READABLE_TIMES):
        fail(
            f"the backfill issued {len(recipe_updates)} UPDATEs for {RECIPE_COUNT} recipes over "
            f"{len(READABLE_TIMES)} readable distinct times; it should scale with distinct times"
        )
    # The control: zero updates would also satisfy the bound above while having
    # backfilled nothing at all.
    if len(recipe_updates) != len(READABLE_TIMES):
        fail(f"expected {len(READABLE_TIMES)} UPDATEs, one per readable distinct time, got {len(recipe_updates)}")

    with sqlite3.connect(DB) as conn:
        backfilled = conn.execute("SELECT COUNT(*) FROM recipes WHERE total_minutes IS NOT NULL").fetchone()[0]
        unreadable = conn.execute(
            "SELECT COUNT(*) FROM recipes WHERE total_time = 'some evening' AND total_minutes IS NULL"
        ).fetchone()[0]
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM recipes WHERE total_minutes < 45 ORDER BY total_minutes"
        ).fetchall()

    expected_backfilled = RECIPE_COUNT - (RECIPE_COUNT // len(DISTINCT_TIMES))
    if backfilled != expected_backfilled:
        fail(f"{backfilled} of {RECIPE_COUNT} rows carry a parsed length, expected {expected_backfilled}")
    if unreadable != RECIPE_COUNT // len(DISTINCT_TIMES):
        fail("rows with an unreadable cook time did not stay NULL")

    # 2. The filter reads the index.
    plan_text = " ".join(str(row) for row in plan)
    if "ix_recipes_total_minutes" not in plan_text:
        fail(f"the cook-time filter does not use the index; plan was: {plan_text}")
    if "SCAN recipes" in plan_text and "USING INDEX" not in plan_text.upper():
        fail(f"the cook-time filter table-scans; plan was: {plan_text}")

    print(f"{RECIPE_COUNT} recipes over {len(DISTINCT_TIMES)} distinct times")
    print(f"backfill issued {len(recipe_updates)} UPDATE(s), one per readable distinct time")
    print(f"{backfilled} rows parsed, {unreadable} left NULL as unreadable")
    print(f"query plan: {plan_text}")
    print("SCALE-OK")


if __name__ == "__main__":
    main()
