# FORK.md — CowDogMoo/mealie

This repository is a household fork of `mealie-recipes/mealie`. It is not a general-purpose
patch set and it is not aiming at upstream. It exists to add one thing — per-person *scoped
meal feedback* — and everything in this document is here so that whoever picks the fork up
next can rebase it, deploy it, roll it back, or retire it without reconstructing the reasoning
from scratch.

- Base: tag `v3.24.0` (the release k8s8 runs).
- Working branch: `woe/scoped-feedback`.
- Design record: `PLAN.md` at the repository root. Where PLAN.md and this file disagree about
  which upstream files the fork touches, **this file is correct** — see the conflict surface
  section below.

Read it end to end before an upgrade. Ten minutes now beats an hour of guessing later.

## Why this fork exists

Stock Mealie v3.24.0 has no dislike primitive at all. Measured against the live instance on
2026-08-31:

- `/openapi.json` contains `dislike` 0 times, `thumb` 0 times, `vote` 0 times. The only
  feedback primitives are per-user stars and favorites: `/api/users/{id}/ratings/{slug}` and
  `/api/users/{id}/favorites/{slug}`.
- Rating reads are self-only. `mealie/routes/users/ratings.py` calls
  `assert_user_change_allowed(id, self.user, self.user)` on every read, which 403s any
  cross-user request. That is why the planner skill's `readPerPersonVotes()` authenticates
  twice, once per 1Password token, just to see both people's votes.
- The meal-plan planner view renders no rating input. On `/household/mealplan/planner/view`
  the star row is display-only: `RecipeCardMobile.vue` uses `RecipeCardRating.vue`, which is
  plain `<span>★</span>` markup, while the interactive `RecipeRating.vue` (`v-rating`) appears
  only on the recipe page. The one screen the household actually looks at each week cannot
  capture a vote.
- Structured reasons have nowhere to live. `users_to_recipes` is
  `(user_id, recipe_id, rating, is_favorite)` under a uniqueness constraint — one mutable row
  per pair. No history, no reason, no scope, so there is nothing to count repeats against.

What the household needs is a thumbs-down that records **which of 12 reasons**, **how wide
that reason licenses the learning to reach**, and **how many times it has been said**.
That vocabulary already existed, but only in the planner CLI (`record-feedback.mjs`), which
nobody opens at the dinner table: zero feedback instances were recorded before this fork.

## What this fork adds

**A table.** `user_recipe_feedback` — an append-only event log, many rows per (user, recipe),
carrying `vote`, `reason`, `scope`, `target`, `note` and a computed `advisory` flag. It is a
new table rather than columns on `users_to_recipes` because confidence-by-repetition needs
history, and because leaving upstream's hottest table alone keeps rebases cheap. The model
lives in `mealie/db/models/users/user_recipe_feedback.py`.

**Five endpoints.**

```
POST   /api/users/{id}/feedback/{slug}        write one event        (self only)
GET    /api/users/{id}/feedback               own events             (self only)
DELETE /api/users/{id}/feedback/{event_id}    undo own event         (self only)
GET    /api/households/feedback               every member's events  (household read)
GET    /api/households/ratings                every member's ratings (household read)
```

The two household reads are the fork's real leverage: within a household, meal plans, shopping
lists and cookbooks are already shared, so reading a housemate's vote is not a new disclosure.
Writes stay self-only — `assert_user_change_allowed` is unchanged on every write path, and
there is no cross-group reach. `GET /api/households/ratings` is what retires the two-token
dance in the planner.

**A vocabulary.** Twelve reasons, five scopes (`recipe`, `dish`, `ingredient`, `cuisine`,
`source`), and the map of which reason licenses which scope:

| reason | licensed scopes |
|---|---|
| `i-did-not-like-this-recipe` | recipe |
| `i-do-not-like-this-dish` | recipe, dish |
| `i-do-not-like-a-specific-ingredient` | recipe, ingredient |
| `too-much-work` | recipe |
| `took-too-long` | recipe |
| `too-heavy` | recipe |
| `not-flavorful-enough` | recipe |
| `too-spicy` | recipe |
| `too-repetitive` | recipe, dish, cuisine |
| `bad-source` | recipe, source |
| `did-not-work-for-our-household` | recipe |
| `other` | recipe |

`mealie/schema/user/user_feedback.py` is the **copy of record** for that list. The planner
skill (`personal-skills/plan-weekly-dinners`, `history.mjs`) holds a byte-identical copy; if
the two ever drift, the fork wins and the skill gets fixed. A scope wider than its reason
licenses is stored with `advisory = true`, never rejected — refusing it would just train
people to pick a broader reason than they mean.

**UI.** Thumbs-up / thumbs-down controls on the meal-plan card and on the recipe page, with a
reason dialog. New components (`RecipeFeedbackButtons.vue`, `RecipeFeedbackDialog.vue`);
existing components gain one prop, `show-feedback`, default `false`, so the change is opt-in
and inert everywhere the planner view does not switch it on.

**An image.** `ghcr.io/cowdogmoo/mealie`, built by `.github/workflows/woe-image.yml`.
Upstream's publish pipeline cannot be reused: it runs through Depot.dev with the maintainers'
Docker Hub secrets and its caller is gated on `github.repository == 'mealie-recipes/mealie'`.

One deliberate coupling to know about: a thumbs-down also writes the caster's
`users_to_recipes.rating` to 1, and a thumbs-up writes 5. The planner's existing `scoreWeek`
already reads ≤2 as a dislike and ≥4 as a like, so nothing downstream had to change on day
one. The feedback row is the record; the star is a projection of it.

## Conflict surface

```
mealie/db/models/users/__init__.py
mealie/repos/repository_recipes.py
mealie/repos/repository_users.py
mealie/repos/repository_factory.py
mealie/routes/users/__init__.py
mealie/routes/households/__init__.py
mealie/schema/user/__init__.py
tests/utils/api_routes/__init__.py
frontend/app/lib/api/types/user.ts
frontend/app/lib/api/user/users.ts
frontend/app/lib/api/user/households.ts
frontend/app/composables/use-users/index.ts
frontend/app/composables/use-clear-composable-caches.ts
frontend/vitest.config.js
frontend/app/components/Domain/Recipe/RecipeCardMobile.vue
frontend/app/components/Domain/Recipe/RecipePage/RecipePageParts/RecipePageInfoCard.vue
frontend/app/pages/household/mealplan/planner/view.vue
frontend/app/lib/icons/icons.ts
frontend/app/lang/messages/en-US.json
```

That block is every *upstream* file this fork modifies, and therefore the entire predictable
rebase cost. Everything else the fork adds is a new file — new models, schemas, repositories,
route modules, Vue components, tests, the alembic revision, the image workflow — and a new
file never conflicts. Keep it that way. If the fork ever starts editing an eighteenth upstream
file, add it here in the same commit, or the next upstream bump becomes a surprise.

Most of the edits are one or two lines: an import and an `include_router` call in the two
route `__init__.py` files, a repository property in the factory, a delete statement in
`repository_recipes.py` (upstream already deletes `UserToRecipe` rows before deleting a recipe
to avoid stale-data errors; ours needs the same treatment or recipe deletion starts failing),
a prop on `RecipeCardMobile.vue`, four `mdiThumb*` entries in `icons.ts`, and new keys in
`en-US.json`. Only `en-US` — every other locale is Crowdin-managed and must not be touched.

Three entries in that list are **generated by `task dev:generate` and must never be
hand-edited**:

- `mealie/schema/user/__init__.py`
- `tests/utils/api_routes/__init__.py`
- `frontend/app/lib/api/types/user.ts`

If a rebase conflicts in one of those, do not merge the hunks. Take upstream's version, re-run
`task dev:generate`, and commit the result.

One generated file is deliberately *not* in the block: `dev/code-generation/openapi.json`.
`task dev:generate` writes it (`gen_py_pytest_routes.dump_open_api()`) and then reads it back
to emit `tests/utils/api_routes/__init__.py`, but it is listed in `.gitignore` and has never
been tracked, so it cannot conflict and is not rebase cost. Worth knowing only so that its
absence from a fresh checkout does not look like a missing file.

PLAN.md §7 has its own file list. It is a **subset** and it is out of date: it omits
`mealie/db/models/users/__init__.py`, `frontend/app/lib/api/user/households.ts`,
`frontend/app/composables/use-users/index.ts` and the three generated files. Use the block
above, not §7.

## Alembic facts

- Base tag: `v3.24.0`.
- Upstream head at that tag: `2187537c52b8` (the AI-providers migration).
- Our revision: `b7d3a1f0c2e4`, `down_revision = "2187537c52b8"`, one `CREATE TABLE` for
  `user_recipe_feedback`.
- **There must always be exactly one head.** `mealie/db/init_db.py` calls
  `command.upgrade(cfg, "head")` on every boot; with two heads that is ambiguous and the
  container never comes up.

Check it with:

```
PRODUCTION=false uv run alembic --config mealie/alembic/alembic.ini heads
```

Expected output: a single line, `b7d3a1f0c2e4 (head)` (on an untouched upstream checkout it
prints `2187537c52b8 (head)` instead). A `Secrets directory '/run/secrets' does not exist`
warning above it is normal on a laptop. The `PRODUCTION=false` is not decoration — without it
Mealie's settings resolve the data directory to `/app/data`, and the command dies with
`OSError: Read-only file system: '/app'` before alembic gets a word in.

## Image facts

- Repository: `ghcr.io/cowdogmoo/mealie`.
- Tags: `v3.24.0-woe.N` — upstream version, then our build number. Bump `N` for every rebuild
  of the same upstream base.
- **Never `:latest`.** A `:latest` tag implies `imagePullPolicy: Always`, and that is how a
  multi-GB pull takes down the k8s8 cluster. The helmrelease pins an explicit tag with
  `pullPolicy: IfNotPresent`, and it stays that way.
- `linux/amd64` only. Mealie runs on a Proxmox VM; there is no arm64 consumer and building one
  doubles the build time for nothing.
- **The GHCR package is public, and making it public is a manual step.** GHCR creates a
  brand-new package as private when the repository is private, and `GITHUB_TOKEN`
  can push to a package but cannot change its visibility. So the first time anyone pushes a
  fork image, go to GitHub → the `cowdogmoo` org (or user) → Packages → `mealie` → Package
  settings → Change visibility → Public. One time, by hand. The alternative is giving the
  `home-automation` namespace an imagePullSecret, which is more moving parts for an image that
  contains nothing secret.

Skipping the visibility step does not fail the build. It fails the *deploy*, hours later,
with `ImagePullBackOff` on a cluster that pulls with no credentials — and there is nothing in
the workflow log to explain it, because as far as the workflow is concerned the push
succeeded. If a new tag will not pull, check package visibility before you check anything
else.

**Measured on the first real push (2026-09-01, `v3.24.0-woe.dev.8942dce`): the package came out
PUBLIC with no manual step.** `CowDogMoo/mealie` is a public fork, and a package pushed by
`GITHUB_TOKEN` inherited that visibility; `gh api /orgs/CowDogMoo/packages/container/mealie`
reported `"visibility": "public"`, and a manifest fetch using only an anonymous pull token — no
credentials, the way a kubelet with no pull secret does it — returned HTTP 200. An earlier draft
of this file asserted the flip was mandatory regardless of repository visibility. That was wrong.

Do not invert the error and assume it is never needed: this is one observation on one public
repository. If the fork is ever made private, or the package is recreated under different
settings, it can come out private again. **Check it, do not assume either way** — the check is one
command, and the failure mode if you skip it is `ImagePullBackOff` hours after a green build:

```
gh api /orgs/CowDogMoo/packages/container/mealie --jq .visibility
```

Deployment lives in the woe repo:
`kubernetes/apps/home-automation/mealie/app/helmrelease.yaml`.

## Rebasing onto a new upstream release

The fork's cost is paid at every upstream bump. Keep it mechanical and it stays cheap.

1. `git fetch upstream --tags`, then branch `woe/scoped-feedback` onto the new tag
   (`git checkout -b woe/scoped-feedback-<version> vX.Y.Z` and replay, or rebase in place —
   either way the base is a *release tag*, never `mealie-next`).
2. Resolve conflicts only in the files listed under **Conflict surface**. For the four
   generated files, take upstream wholesale and let step 5 regenerate them.
3. Re-point our migration's `down_revision` to the new upstream head. Immediately after the
   rebase, `PRODUCTION=false uv run alembic --config mealie/alembic/alembic.ini heads` will
   print *two* revisions: ours (`b7d3a1f0c2e4`) and the new upstream head, because upstream
   added migrations on top of the old one and ours still hangs off `2187537c52b8`. The one
   that is not ours is the new head. Edit `down_revision` in
   `mealie/alembic/versions/*b7d3a1f0c2e4*.py` to that value. Our own revision id never
   changes — the database is already stamped with it. Update the rollback target in the
   **Rollback trap** section below to the same value in the same commit; a stale downgrade
   target in this file is the thing that bites at 2am.
4. Re-run the same `heads` command and confirm it now returns exactly one revision,
   `b7d3a1f0c2e4 (head)`. Two heads means step 3 did not take, and the container will not
   start.
5. Re-run `task dev:generate`. **This needs `json2ts` on PATH** — install
   `json-schema-to-typescript@13.1.2` globally (`npm install -g json-schema-to-typescript@13.1.2`);
   `task setup` does not do it for you. Pin 13.x: later majors reformat all twelve type
   modules, so a contributor on 16.x produces a diff that looks like the fork rewrote
   upstream's generated types. And know the failure mode — `gen_ts_types.py` catches a
   per-module exception, logs `Failed Modules`, and still exits 0, so without `json2ts` the
   generator produces *nothing* while reporting success. A `task dev:generate &&
   git diff --exit-code` check passes vacuously in that state. Read the log, not the exit code. Upstream schema drift changes the generated files whether or
   not we touched anything; commit whatever it produces.
6. `task py:check` and `task ui:check`. These are upstream's gates and they must be green
   before ours are meaningful.
7. Then the fork's own gates: the migration round-trips
   (`upgrade head` → `downgrade -1` → `upgrade head`, repeated under `task py:postgres` so
   both engines are covered); `task py:test -- -k feedback` passes, including cross-user
   write 403 and cross-household read empty; and, in a browser against a dev instance, the
   planner card renders an enabled thumbs-down, the dialog submits, and the row comes back
   through `GET /api/households/feedback` under the *other* person's token.
8. Bump the image tag to `<new-upstream-version>-woe.1`, let the workflow build and push, then
   update `tag:` in the helmrelease and reconcile. Two things before that reconcile: take a
   backup (see the next section), and — if this is the first image ever pushed from this
   repository, or the package was recreated — confirm the GHCR package is public, per **Image
   facts**. A private package pulls fine from your laptop and not at all from k8s8.

## Rollback trap

**The trap.** Our migration stamps revision `b7d3a1f0c2e4` into the database's `alembic_version`
table. The upstream image has never heard of that revision. So if the fork misbehaves in
production and the reflex is to swap the image back to
`ghcr.io/mealie-recipes/mealie:v3.24.0`, the pod will not start: alembic fails at startup with
`Can't locate revision identified by 'b7d3a1f0c2e4'`. The database is fine, the data is fine,
and the application is down. Swapping the tag back is *not* a rollback. Do not reach for it
first at 2am.

**Before any upgrade.** Run the existing backup CronJob by hand and verify that both the
database dump *and* the asset archive landed on the NAS. Verify — list the files, check the
sizes and timestamps — do not assume the job succeeded because it exited 0. The migration
itself is additive (one `CREATE TABLE`), so the risk is not the migration; the risk is
needing a way back that does not exist yet.

**Rolling back.** Two exits, and only two:

1. *Downgrade in place, then swap.* Exec into the running Mealie pod — it still has our
   image, and is therefore the only container that knows the revision — and run
   `alembic downgrade 2187537c52b8`. One catch: the alembic config ships inside the installed
   package, not the working directory, so the bare CLI will not find its `.ini`. Drive it
   through alembic's API instead, which resolves the path the same way the app does at
   startup:

   ```
   kubectl -n home-automation exec deploy/mealie -- /opt/mealie/bin/python -c "
   from alembic import command
   from alembic.config import Config
   from mealie.db.init_db import ALEMBIC_DIR
   cfg = Config(str(ALEMBIC_DIR / 'alembic.ini'))
   command.downgrade(cfg, '2187537c52b8')
   command.current(cfg)
   "
   ```

   (Adjust the namespace and workload name if the helmrelease renamed them.) That drops
   `user_recipe_feedback` and rewinds `alembic_version`; the trailing `command.current` prints
   what the database is now stamped at, and it must print `2187537c52b8`. Only then change the
   helmrelease back to `ghcr.io/mealie-recipes/mealie:v3.24.0` and reconcile. The feedback
   rows are gone; the stars, meal plans and recipes are not. Do this while the fork's pod is
   still up — once you have swapped the image you no longer have a container that can run the
   downgrade.

   `2187537c52b8` is the target *while the fork is based on v3.24.0*. It is always whatever
   our migration's `down_revision` currently says; check
   `mealie/alembic/versions/*b7d3a1f0c2e4*.py` if you are not sure, or use `downgrade -1`,
   which means the same thing as long as ours is the only fork migration.

2. *Restore the pre-upgrade backup.* Restore the dump and the asset archive taken in the step
   above, then deploy the upstream image. Slower, loses anything written since the backup, but
   it is the exit that works when the pod is already crash-looping and there is no live
   container left to run the downgrade in.

## Kill criterion

If four weeks after the deployment gate (PLAN.md §6, G8 — the fork's image running on k8s8
with `GET /api/households/feedback` answering 200) nobody has pressed the button, stop
carrying the fork.

Check with a single call — `GET /api/households/feedback` with a household token, no filters.
If it returns an empty list, or only rows we wrote ourselves while testing, that is the answer.

Then redeploy stock Mealie using the rollback procedure above (downgrade first, then swap the
image), and keep the CLI. The reason to be strict about this: the CLI nobody opens costs
nothing. A fork that captures nothing costs a rebase every upstream release *and* blocks
upgrades whenever that rebase is inconvenient — it is strictly worse than the thing it
replaced. The whole point of the fork was to put the vote where the household already looks.
If it turns out the household still does not vote, the fork has been disproved, not
under-adopted, and the correct move is to delete it.
