#!/usr/bin/env python
"""The TypeScript types carry totalMinutes, and they are generated rather than typed.

`frontend/app/lib/api/types/` is generated from the Pydantic schemas, and the
project's rule is that nobody edits it by hand. Two things are checked here:

1. `totalMinutes` reached the generated types at all -- the badge and the filter
   both read it, and a hand-added field would vanish at the next generation.
2. Running the generator again changes nothing. This is what would catch a field
   that only exists because somebody typed it into the generated file.

Compares file contents before and after rather than asking git, so it says
something true in a working tree that already has other uncommitted changes.

Run: uv run python dev/scripts/check_codegen_clean.py
"""

# ruff: noqa: T201 -- a command-line check; its printed report is the interface

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GENERATED_DIR = REPO / "frontend/app/lib/api/types"
RECIPE_TYPES = GENERATED_DIR / "recipe.ts"
FIELD = "totalMinutes"


def fail(message: str) -> None:
    print(f"CODEGEN-FAILED: {message}")
    sys.exit(1)


def snapshot() -> dict[Path, str]:
    return {path: path.read_text() for path in sorted(GENERATED_DIR.glob("*.ts"))}


def main() -> None:
    if not RECIPE_TYPES.exists():
        fail(f"{RECIPE_TYPES} does not exist")

    before = snapshot()

    text = RECIPE_TYPES.read_text()
    if FIELD not in text:
        fail(f"{FIELD} is not in {RECIPE_TYPES.relative_to(REPO)}; run `task dev:generate`")

    # It has to be on the summary too, not only the full recipe: the browse grid
    # renders summaries, and that is where the badge lives.
    interfaces = [line for line in text.splitlines() if line.startswith("export interface ")]
    for required in ("export interface Recipe ", "export interface RecipeSummary "):
        if not any(line.startswith(required) for line in interfaces):
            fail(f"{required.strip()} is missing from the generated types")

    occurrences = text.count(f"{FIELD}?:")
    if occurrences < 2:
        fail(f"{FIELD} appears {occurrences} time(s); expected it on both Recipe and RecipeSummary")

    # The generator imports app settings, which try to create /app/data unless
    # this is a development run.
    result = subprocess.run(
        ["uv", "run", "python", "dev/code-generation/main.py"],
        cwd=REPO,
        env={**os.environ, "PRODUCTION": "false"},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(f"the generator exited {result.returncode}\n{result.stdout}\n{result.stderr}")

    after = snapshot()

    if set(before) != set(after):
        added = sorted(p.name for p in set(after) - set(before))
        removed = sorted(p.name for p in set(before) - set(after))
        fail(f"the generator added {added} and removed {removed}")

    changed = sorted(path.name for path, text_before in before.items() if after[path] != text_before)
    if changed:
        fail(f"re-running the generator rewrote {changed}, so the checked-in types were not generated from the schemas")

    print(f"{FIELD} present on Recipe and RecipeSummary; {len(after)} generated files unchanged by a fresh run")
    print("CODEGEN-OK")


if __name__ == "__main__":
    main()
