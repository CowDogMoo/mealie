#!/usr/bin/env python
"""Prove the total_minutes migration on a throwaway database.

The backfill is the part of this change that runs once, unattended, against a
library somebody already has. So it is checked the way it will actually run: a
real Alembic upgrade from the revision before it, over rows holding the cook-time
forms the household's own recipes use, then a downgrade back.

Absence is controlled. "some evening" must come out NULL, but a checker that
wrote nothing at all would also produce NULL -- so the same run asserts the
parseable rows came out with numbers. One without the other proves nothing.

Run: uv run python dev/scripts/check_total_minutes_migration.py
"""

# ruff: noqa: T201 -- a command-line check; its printed report is the interface

import os
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "d4a8b2c6e9f1"
ALEMBIC_INI = "mealie/alembic/alembic.ini"

# (total_time as stored, expected total_minutes after the backfill)
CASES = [
    ("30 minutes", 30),
    ("22 minutes", 22),
    ("1 Hour 15 Minutes", 75),
    ("PT50M", 50),
    ("PT1H30M", 90),
    ("45 min", 45),
    ("40", 40),
    # The negative case, and the reason the positives above are asserted in the
    # same run: an unreadable time must stay NULL rather than become a number.
    ("some evening", None),
    ("", None),
    (None, None),
]


def alembic(env: dict[str, str], *args: str) -> None:
    result = subprocess.run(
        ["uv", "run", "alembic", "--config", ALEMBIC_INI, *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"alembic {' '.join(args)} exited {result.returncode}\n{result.stdout}\n{result.stderr}")


def fail(message: str) -> None:
    print(f"MIGRATION-FAILED: {message}")
    sys.exit(1)


def columns(db: Path) -> list[str]:
    with sqlite3.connect(db) as conn:
        return [row[1] for row in conn.execute("PRAGMA table_info(recipes)")]


def main() -> None:
    scratch = Path("tests/.temp") / f"total-minutes-{uuid.uuid4().hex[:8]}"
    (REPO / scratch).mkdir(parents=True, exist_ok=True)
    db = REPO / scratch / "mealie.db"

    env = {**os.environ, "TESTING": "true", "PRODUCTION": "false", "DATA_DIR": str(scratch)}

    # 1. The database as it stands before this change.
    alembic(env, "upgrade", PREVIOUS_REVISION)
    if "total_minutes" in columns(db):
        fail("total_minutes already exists at the previous revision, so the upgrade proves nothing")

    # 2. Rows in the shape a real library holds them.
    group_id, user_id = uuid.uuid4().hex, uuid.uuid4().hex
    with sqlite3.connect(db) as conn:
        for index, (total_time, _expected) in enumerate(CASES):
            name = f"Probe {index}"
            conn.execute(
                "INSERT INTO recipes "
                "(id, slug, name, name_normalized, group_id, user_id, total_time, "
                " recipe_yield_quantity, recipe_servings) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)",
                (uuid.uuid4().hex, f"probe-{index}", name, name.lower(), group_id, user_id, total_time),
            )

    # 3. The upgrade under test.
    alembic(env, "upgrade", "head")

    if "total_minutes" not in columns(db):
        fail("the upgrade did not add total_minutes")

    with sqlite3.connect(db) as conn:
        stored = dict(conn.execute("SELECT slug, total_minutes FROM recipes").fetchall())
        indexes = [row[1] for row in conn.execute("PRAGMA index_list(recipes)")]

    parsed = 0
    for index, (total_time, expected) in enumerate(CASES):
        actual = stored.get(f"probe-{index}")
        if actual != expected:
            fail(f"{total_time!r} backfilled as {actual!r}, expected {expected!r}")
        if expected is not None:
            parsed += 1

    # The control on the NULL assertions above: if the backfill had written
    # nothing at all, every row would be NULL and every negative case would still
    # "pass". These are the rows that say it ran.
    expected_parsed = sum(1 for _, expected in CASES if expected is not None)
    if parsed != expected_parsed:
        fail(f"only {parsed} of {expected_parsed} readable times were backfilled")

    if not any("total_minutes" in name for name in indexes):
        fail(f"total_minutes is not indexed; the browse filter would table-scan. indexes: {indexes}")

    # 4. And back down again, because a migration you cannot reverse is a
    #    migration you cannot deploy twice.
    alembic(env, "downgrade", PREVIOUS_REVISION)
    if "total_minutes" in columns(db):
        fail("the downgrade left total_minutes behind")
    with sqlite3.connect(db) as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
    if remaining != len(CASES):
        fail(f"the downgrade lost recipes: {remaining} of {len(CASES)} remain")

    print(f"checked {len(CASES)} cook-time forms, {expected_parsed} parsed, index present, downgrade clean")
    print("MIGRATION-OK")


if __name__ == "__main__":
    main()
