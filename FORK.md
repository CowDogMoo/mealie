# FORK.md — CowDogMoo/mealie

This repository is a household fork of `mealie-recipes/mealie`. It is not a general-purpose
patch set and it is not aiming at upstream. It exists to add one thing — per-person *scoped
meal feedback* — and everything in this document is here so that whoever picks the fork up
next can rebase it, deploy it, roll it back, or retire it without reconstructing the reasoning
from scratch.

- Base: `mealie-next` at `14e66291` (`v3.24.0-41`) — see **Alembic facts**.
- Working branch: `woe/scoped-feedback`, merged into `mealie-next` as `dc19f800`.
- Design record: `PLAN.md` at the repository root. Where PLAN.md and this file disagree about
  which upstream files the fork touches, or about which base the fork sits on, **this file is
  correct** — it is the one that gets checked against git. See the conflict surface section
  below.

**That base is not the one PLAN.md §3 D6 specifies, and the difference matters.** D6 says to
branch from tag `v3.24.0`, "not `mealie-next`", so that what runs in production is a release plus
our stack. The fork was originally cut that way — `8942dce0` sits on the tag with
`down_revision = "2187537c52b8"`, exactly as D6 asks — and was then moved onto `mealie-next` in
`75a68a13` for the reason given in step 1 of **Rebasing onto a new upstream release**, and merged
there as `dc19f800`. The move was deliberate; it has never been reconciled with D6, and PLAN.md
still says the opposite. Two consequences to carry: what production would run is a development
branch, not a release, and the `v3.24.0-woe.N` tag scheme under **Image facts** names a base the
tree is no longer on.

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

**UI.** Thumbs-up / thumbs-down controls on the meal-plan card, on the recipe page, and on
every browse grid `RecipeCardSection.vue` draws (the recipes list, favorites, cookbooks,
search, and organizer pages), with a reason dialog. New components
(`RecipeFeedbackButtons.vue`, `RecipeFeedbackDialog.vue`); existing components gain one prop,
`show-feedback`, default `false`, so the change is opt-in and inert everywhere a caller does
not switch it on. A thumbs-down also takes the recipe out of the voter's own browse grids:
`RecipeCardSection.vue` filters, per viewer and on the client, every recipe whose newest event
from that viewer hides it (`hiddenRecipeIds` in `frontend/app/composables/use-users/feedback-log.ts`
is the rule), and shows a "N hidden" chip in its toolbar that toggles them back into view. The
recipe itself is untouched — a housemate still sees it — and the vote is undone from the My
Recipe Feedback page (`/user/profile/feedback`), which also re-projects the star the vote wrote.

**An image.** `ghcr.io/cowdogmoo/mealie`, built by `.github/workflows/woe-image.yml`.
Upstream's publish pipeline cannot be reused: it runs through Depot.dev with the maintainers'
Docker Hub secrets and its caller is gated on `github.repository == 'mealie-recipes/mealie'`.

**A cart request.** A shopping list can be sent to the household's grocery cart from the list
page: `<ShoppingListCartButton>` asks for confirmation naming how many un-ticked items would be
bought, then `POST /api/households/shopping/lists/{id}/cart-request` records the ask. `DELETE`
cancels one that is still waiting or dismisses one that finished; `PUT` is how a filler claims,
finishes or fails it; and `GET /api/households/shopping/cart-requests` is what the cluster runner
polls. **None of it orders anything** — the cart is filled by something else and a person checks
out. The state is four to eight string keys in the list's own `extras` (`cartRequest*`), so there
is no new table and no migration; `mealie/schema/household/cart_request.py` is the copy of record
for those keys and the `pending -> filling -> filled | failed` vocabulary, and
`frontend/app/composables/use-cart-request.ts` holds the TypeScript copy, with a test that fails
if the two drift. One list may owe only one fill at a time, a list with nothing un-ticked is
refused rather than queued, and a report carrying a request id the list no longer holds is
refused so a late fill cannot claim a request nobody made.

**A recipe request.** The Recipes page carries a "Find a new recipe" button, with an optional
note ("something with lentils"). `POST /api/households/recipe-requests` records the ask in its
own table, `household_recipe_requests` (revision `f6c1d8e3b597`); the cluster runner polls
`GET ?status=pending`, claims a request with `PUT` (`filling`), and reports it `filled` with the
slug of the recipe it imported, or `failed` with why. A `filled` report is refused unless that
slug is really in the household's recipes, so the page never announces a recipe that did not
land. `DELETE` cancels a waiting request or dismisses a finished one. It is the die on a recipe
card without the recipe: nothing is hidden, rated or planned. A household may have five
waiting at once (`MAX_OPEN_RECIPE_REQUESTS`), because each is a recipe search on the household's
Claude subscription. `mealie/schema/household/recipe_request.py` is the copy of record for the
statuses and the note limit; `frontend/app/composables/use-recipe-requests.ts` holds the
TypeScript copy, with a test that fails if the two drift. The upstream edits are a
`below-toolbar` slot in `RecipeCardSection.vue`, the bar in that slot on
`RecipeExplorerPage.vue`, and the usual model, repository, router and client registrations.

**An x.** Beside the thumbs and the die, every card carries an "x": take this recipe off my list
without saying anything about it. It posts a `dismiss` event (`DISMISS_VOTE` in
`mealie/schema/user/user_feedback.py`) through the same `POST /api/users/{id}/feedback/{slug}`
as a vote. It asks for no reason, writes no star and moves no thumb; the recipe leaves the
presser's own grids the way a thumbs-down does, My Recipe Feedback lists it under "Removed" where
it can be undone, and the planner skill reads it from the household feed and stops suggesting the
recipe. A card can stay on screen after its x (a planned night, or the grid with hidden recipes
shown), so the x can be pressed again: the route stores nothing for a `dismiss` whose caller's
newest event on that recipe is already a `dismiss` and answers with that one, and undoing a
`dismiss` deletes every `dismiss` that person has on the recipe, so one undo is all it takes to
bring the recipe back. **The planner must know the vote before the fork ships it**: `history.mjs`
throws on a vote it has never heard of, so a `dismiss` event reaching an old runner fails every
sync tick. Deploy the runner first.

**Pantry staples.** A household marks the foods it usually has -- salt, sugar, olive oil -- and
recipes leave them off the shopping list. There is no new storage: a staple is upstream's
per-household "on hand" flag (`households_to_ingredient_foods`), which the shopping list service
already skips when a recipe is added, so the add-to-list dialog, the foods page and the recipe
finder all agree with the list without knowing it exists. What the fork adds is the part around
the flag. `GET /api/households/pantry` lists the staples and suggests foods that look like ones;
`PUT` and `DELETE /api/households/pantry/{food_id}` write one household's flag and nothing else
(upstream can only replace a food's whole list of households); and
`GET /api/households/pantry/shopping-lists/{id}` reports the staples a list's recipes use, which
`<ShoppingListPantryLeftOff>` shows above the items with a tap to put one back, because "usually"
is not "always". Marking a staple also marks its *plain forms* -- the same name with only words
like "kosher", "fine" or "extra-virgin" in front -- and `POST /api/foods` gives a newly created
plain form the flag too (`PantryService.inherit`), since recipe imports create a new spelling of
salt most weeks. Any other word in front ("brown sugar", "garlic salt") is only ever suggested.
`mealie/schema/household/pantry.py` is the copy of record for that rule and its word list. The
upstream edits are the `inherit` call in `routes/unit_and_foods/foods.py`, and upstream's own
later `skip_on_hand` fix ported into `services/household_services/shopping_lists.py` so that an
ingredient somebody ticks by hand in the add-to-list dialog is added even when it is a staple;
drop that port when a rebase brings the fix in.

**Google Calendar.** A household can mirror its meal plan onto one Google calendar, so dinner is
where people already look and nobody has to open Mealie's planner to see it. Mealie's plan stays
the truth -- the planner, Home Assistant and the votes all read it -- and exactly one thing is read
back from Google: the day of an event somebody moved (below). It is a push through the Calendar API and not an ICS feed
for two reasons that are about this household: the instance is reachable only on the LAN, so
Google could not fetch a feed, and a dinner is swapped minutes after a thumbs-down, which a feed
polled twice a day would show wrong. The credential is server-wide, in a new setting,
`GOOGLE_CALENDAR_CREDENTIALS`: the JSON of a service-account key, and of nothing else, traded for
access tokens with `httpx` and `PyJWT` (`mealie/services/google_calendar/credentials.py`); no
Google client library was added. Server-wide is meant literally: nothing ties a calendar to the
household that shared it, so anyone who can manage any household on the instance can point its
mirror at any calendar the service account can write. That suits an instance with a single
household, which this one is, and is not a boundary between households. Which calendar, and
whether the mirror is on, is one row per household in `household_calendar_sync` (revision
`a7e2d4f9c6b3`), set on `/household/google-calendar` through `GET` and
`PUT /api/households/calendar-sync`. Turning it on is refused unless the calendar can be opened and
written to there and then, and the refusal names the service account's address, which is what the
household shares its calendar with ("Make changes to events"). Events already written stay in the
old calendar when the mirror is switched to another calendar or turned off; nothing goes back to
remove them. **A sync is a reconcile, not a push of one change**
(`mealie/services/household_services/calendar_sync.py`): read the plan from
yesterday onward, list the calendar's events that carry this household's private extended property
(`mealieHousehold`, with `mealieEntry` naming the plan entry), then add what is missing, patch what
differs, and delete what has no entry or is a second copy of one. An event without the household's
property is never touched, and events before yesterday are left as a record of what was eaten. No
event id is stored in Mealie, so there is nothing to drift. A dinner is a timed event from 6:00 PM
to 8:00 PM on its date; every other kind of meal is an all-day event; both are marked free, so
neither blocks anyone's time. The hours are in the server's time zone, the `TZ` the container is
started with (it has to be `America/Denver` for this household), because Mealie keeps no
time zone per household; the offset is worked out for the date itself, so dinner stays
at six across a daylight-saving change. With `TZ` unset, or not the name of an IANA time
zone, a sync changes nothing on the calendar and fails with a `last_error` that names
the `TZ` to fix. A dinner an older version wrote as an all-day event is patched into its
hours by the next sync, in place. Three things set a sync off: the meal
plan's own `mealplan_entry_*` events on the event bus (a new listener, run as a background task
after the response, so a Google failure can neither fail nor slow a plan change);
`POST /api/households/calendar-sync/sync`, the page's "Sync now"; and a scheduler task every five
minutes (registered with upstream's "minutely" tasks, which is what that interval is called),
which is what notices a dinner moved in Google Calendar and also heals whatever the other two
missed, including changes nothing announces, like a recipe being renamed. Syncs are serialized per household and coalesced, in this process only, because the
planner replaces a week in a burst of fourteen writes; the duplicate removal is what puts things
right if two processes ever overlap. The latest failure is kept on the row (`last_error`), shown
on the page, and cleared by the next sync that works. The upstream edits are the ones listed for
Google Calendar under "Conflict surface" below, plus the usual model, repository, router and
client registrations.

Turning it on is three steps, and only the first needs whoever runs the server:

1. Set `GOOGLE_CALENDAR_CREDENTIALS` to the whole JSON of a service-account key file (the one
   Google Cloud downloads: `type`, `client_email`, `private_key`, `token_uri`, ...) and restart.
   The Google Cloud project behind the key needs the Calendar API enabled. Any file named
   `GOOGLE_CALENDAR_CREDENTIALS` under `/run/secrets` is read the same way. The value is masked in
   the settings dump at startup, and no route returns it. The same container needs its `TZ` set
   to the household's time zone (above); without it every sync fails.
2. In Google Calendar, share the calendar with the key's `client_email` -- the page shows it, with
   a copy button -- and choose "Make changes to events".
3. On `/household/google-calendar`, paste the calendar's id (Settings > Integrate calendar), switch
   the mirror on, save, and press "Sync now" to fill the calendar straight away.

**Moving a dinner in Google Calendar moves it in Mealie.** Google Calendar is the easy place to
drag Tuesday's dinner to Thursday, so that is the one edit made there that counts. Every sync
starts by reading the mirror's events for ones on another day than Mealie put them, and moves each
such plan entry to the event's day before it pushes the plan (`CalendarSyncService._pull_moves`).
The sweep runs every five minutes, so a move made in Google reaches Mealie within about that; "Sync
now" does it at once. The rules:

| Done in Google Calendar                | What Mealie does                                                        |
| -------------------------------------- | ----------------------------------------------------------------------- |
| Event moved to another day             | Moves the meal to that day; the event is set to that day's 6 to 8 PM     |
| Time changed, same day                 | Nothing; the event is put back to 6 to 8 PM                              |
| Title or description changed           | Nothing; the event's text is put back to the plan's                      |
| Event deleted                          | Nothing is deleted; the event is written again                           |
| Moved there *and* in Mealie since the last sync | Mealie's day stands; the event is put on it                    |

Only the day comes back, and it is read in the server's `TZ`, the same zone the event was written
in. A move is recognized by a third private property on every event, `mealieDate`, the day Mealie
last put the event on: dragging an event changes its day and leaves the property, so the two
disagree only when the move was made in Google. If the entry's own date still equals `mealieDate`,
only Google moved it, and the entry follows. If the entry's date changed too (Mealie's push failed,
or has not run yet), Mealie moved it as well, and Mealie's day stands: the push that follows puts
the event there. Things to know:

- An event written before this existed has no `mealieDate`, and a different day on it is not read
  as a move. The first sync after the upgrade stamps every event in the window (one patch each),
  and moves are followed from then on.
- A meal moved this way is written straight to the plan and does not go out on the event bus, so
  notifiers and webhooks subscribed to meal plan updates do not hear about it.
- The calendar is read back to midnight UTC three days ago, which west of UTC, where this
  household is, still takes in the day before that. An event dragged further into the past is no
  longer seen, so its meal stays where it was and gets a new event there.
- A dinner dragged to a day inside that reach moves there like any other, even though the
  day is before the window the mirror otherwise leaves alone ("when we cooked it"). That one sync
  sets the event to the day's 6 to 8 PM and to the day as its `mealieDate`; after it the event is
  a record like any other eaten dinner. Dragging it forward again moves the meal forward again.
- A copy of an event made in Google Calendar ("Duplicate") carries the same properties. It is not
  a second meal: the sync keeps the older event and deletes the copy.

The planner page has an **"Open Family calendar"** button beside its settings link
(`<GroupMealPlanCalendarLink>`) whenever the household's mirror is on. It opens
`https://calendar.google.com/calendar/r?cid=<calendar id>` in a new tab: the Google Calendar app
on a phone that has it, the site otherwise. Nothing of Google's is shown inside Mealie.

That button and the move read-back above are what this fork has in place of showing Google
Calendar inside the planner. An embedded calendar was built first, behind a server setting,
`MEAL_PLAN_CALENDAR_VIEW`, and dropped: Google's embed cannot load a private calendar inside Mealie
on an iPhone (it fails in the home-screen app, and Safari wants a Google sign-in and cookie
prompts first). Neither the embed nor the setting exists, and no server setting took its place.
The switch is the household's own, on `/household/google-calendar`: with a calendar linked and the
mirror on, the button shows and moves made in Google are read back; with the mirror off, or no
calendar linked, neither happens.

**Drag a meal to another day.** Moving a dinner is a drag on the planner's default "Meal Planner"
tab, not an edit of the entry's date. Upstream already had dragging, but only on the Edit tab, and
on a phone only by the little arrows button. Each day on `planner/view.vue` is now a
`vue-draggable-plus` list (the library the Edit tab already used, in the same `meals` group), a
whole card is the handle, and dropping it on another day saves the entry with that day's date
through the same `updateOne` the date field uses, so the calendar sync pushes the move as it would
any date edit. The drop logic lives in `composables/use-meal-plan-drag.ts`, which holds the options
(touch needs a 250 ms press before a card lifts, so swiping to scroll never picks one up) and
`handleMealDrop`; the Edit tab is left as upstream has it, with its own drop handler. Each
day's list carries its date as `data-date` and each card its entry id as `data-entry-id`. The lists
do not reorder within a day, because the view orders a day by meal type; an entry keeps its meal
type when it moves. A card shows on its new day, in its meal-type place, as soon as it is dropped,
before the save comes back. Every drop on another day is saved, even onto the date the plan still
has for the entry, because the plan lags behind while an earlier move is being saved. If the save
fails the view rebuilds its lists from the plan, which puts the card back; to know that,
`updateOne` in `composables/use-group-mealplan.ts` now returns whether the save landed, the only
change this feature makes to that upstream file. Verified by hand at 390x844 with touch emulation
(iPhone Safari user agent, which makes SortableJS use touch events as it does on the phone): a long
press and drag moved a dinner to another day and to an empty one, both saved and survived a reload,
and a quick swipe over a card scrolled without lifting it. A mouse drag was not driven by hand,
because native HTML5 drag-and-drop cannot be synthesized from the browser tooling used; it goes
through the same SortableJS path the Edit tab has always used.

One deliberate coupling to know about: a thumbs-down also writes the caster's
`users_to_recipes.rating` to 1, and a thumbs-up writes 5. The planner's existing `scoreWeek`
already reads ≤2 as a dislike and ≥4 as a like, so nothing downstream had to change on day
one. The feedback row is the record; the star is a projection of it.

## Conflict surface

```
mealie/app.py
mealie/core/settings/settings.py
mealie/db/models/group/group.py
mealie/db/models/household/__init__.py
mealie/db/models/household/household.py
mealie/db/models/users/__init__.py
mealie/repos/repository_recipes.py
mealie/repos/repository_shopping_list.py
mealie/repos/repository_users.py
mealie/repos/repository_factory.py
mealie/routes/users/__init__.py
mealie/routes/households/__init__.py
mealie/routes/unit_and_foods/foods.py
mealie/services/backups_v2/alchemy_exporter.py
mealie/services/event_bus_service/event_bus_service.py
mealie/services/household_services/shopping_lists.py
mealie/schema/household/__init__.py
mealie/schema/recipe/__init__.py
mealie/schema/user/__init__.py
tests/utils/api_routes/__init__.py
frontend/app/lib/api/types/household.ts
frontend/app/lib/api/types/meal-plan.ts
frontend/app/lib/api/types/recipe.ts
frontend/app/lib/api/types/user.ts
frontend/app/lib/api/client-user.ts
frontend/app/lib/api/user/users.ts
frontend/app/lib/api/user/households.ts
frontend/app/composables/use-users/index.ts
frontend/app/composables/use-clear-composable-caches.ts
frontend/app/composables/use-group-mealplan.ts
frontend/vitest.config.js
frontend/app/components/Domain/Recipe/RecipeCard.vue
frontend/app/components/Domain/Recipe/RecipeCardMobile.vue
frontend/app/components/Domain/Recipe/RecipeCardSection.vue
frontend/app/components/Domain/Recipe/RecipeExplorerPage/RecipeExplorerPage.vue
frontend/app/components/Domain/Recipe/RecipePage/RecipePageParts/RecipePageInfoCard.vue
frontend/app/pages/household/mealplan/planner.vue
frontend/app/pages/household/mealplan/planner/view.vue
frontend/app/pages/shopping-lists/[id].vue
frontend/app/pages/user/profile/index.vue
frontend/app/lib/api/user/group-shopping-lists.ts
frontend/app/lib/icons/icons.ts
frontend/app/lang/messages/en-US.json
```

That block is every *upstream* file this fork modifies, and therefore the entire predictable
rebase cost. Everything else the fork adds is a new file — new models, schemas, repositories,
route modules, Vue components, tests, the alembic revision, the image workflow — and a new
file never conflicts. Keep it that way. If the fork ever starts editing another upstream
file, add it here in the same commit, or the next upstream bump becomes a surprise.

Most of the edits are one or two lines: an import and an `include_router` call in the two
route `__init__.py` files, a repository property in the factory, a delete statement in
`repository_recipes.py` (upstream already deletes `UserToRecipe` rows before deleting a recipe
to avoid stale-data errors; ours needs the same treatment or recipe deletion starts failing),
a prop on each of the two recipe cards plus the `show-feedback` pass-through in
`RecipeCardSection.vue`, four `mdiThumb*` entries in `icons.ts`, and new keys in
`en-US.json`. Only `en-US` — every other locale is Crowdin-managed and must not be touched.

The three shopping-list entries come from **cart requests** (below): a `set_extras` method on
`repository_shopping_list.py`, two methods and a route on the shopping list API client, and the
`<ShoppingListCartButton>` line on the list page. The rest of that feature is new files.

Five entries come from **Google Calendar**, and each is a line or two: the
`<GroupMealPlanCalendarLink />` line and its import in the planner page (`mealplan/planner.vue`), the
`GOOGLE_CALENDAR_CREDENTIALS` field at the end of `AppSettings` in `core/settings/settings.py`, the
`CalendarSyncEventListener` import and list entry in `event_bus_service.py`, the
`sync_google_calendars` import and `register_minutely` entry in `app.py`, and `last_synced_at` in
`AlchemyExporter.look_for_datetime`. That last one is not optional: a restore rebuilds datetimes by
column name, so a datetime column missing from that set comes back from a backup as a string
(upstream's `test_every_datetime_column_survives_a_backup` fails until it is added). The task is
imported straight from its own module so that `services/scheduler/tasks/__init__.py` stays
upstream's.

The prose in this section walks through the scoped-feedback edits specifically. Features shipped
after `v3.24.0-woe.5` — the recipe source list, the "find me a new one" die, cart requests — have
their files in the block above but are not all described here; the block is what a rebase needs.

Seven entries in that list are **generated by `task dev:generate` and must never be
hand-edited**:

- `mealie/schema/recipe/__init__.py`
- `mealie/schema/user/__init__.py`
- `tests/utils/api_routes/__init__.py`
- `frontend/app/lib/api/types/household.ts`
- `frontend/app/lib/api/types/meal-plan.ts`
- `frontend/app/lib/api/types/recipe.ts`
- `frontend/app/lib/api/types/user.ts`

If a rebase conflicts in one of those, do not merge the hunks. Take upstream's version, re-run
`task dev:generate`, and commit the result.

Four of those seven carry no fork content at all: `mealie/schema/recipe/__init__.py` and the
`household.ts` / `meal-plan.ts` / `recipe.ts` type modules gained `CategoryMerge`, `TagMerge` and
`recipeCount` — upstream models that already existed in upstream's Python sources at `14e66291`
while upstream's own committed generated files were stale. Running the generator materialised the
difference and the fork now carries it. Expect that every time: the generator regenerates
everything, so a rebase can widen this list without anyone writing a line of fork code. Re-derive
the block from git after the codegen step, do not trust it from memory.

One generated file is deliberately *not* in the block: `dev/code-generation/openapi.json`.
`task dev:generate` writes it (`gen_py_pytest_routes.dump_open_api()`) and then reads it back
to emit `tests/utils/api_routes/__init__.py`, but it is listed in `.gitignore` and has never
been tracked, so it cannot conflict and is not rebase cost. Worth knowing only so that its
absence from a fresh checkout does not look like a missing file.

PLAN.md §7 has its own file list, at 18 paths. It is a **subset** of the block above and it is
out of date: it omits `frontend/vitest.config.js`, `mealie/schema/recipe/__init__.py` and the
three regenerated `frontend/app/lib/api/types/{household,meal-plan,recipe}.ts` modules. §7 also
still says a gate proves the block equals `git diff --name-only v3.24.0`; that comparison stopped
meaning anything when the fork moved off the tag — against `v3.24.0` the diff is now roughly 200
paths of upstream history the fork never touched. Use the block above, compared against the real
base, not §7.

## Alembic facts

- Base: `mealie-next` at `14e66291` (`v3.24.0-41`), rebased 2026-08-31.
- Upstream head at that commit: `69e942bab3aa` (the `tokens_valid_after` migration).
- Our first revision: `b7d3a1f0c2e4`, `down_revision = "69e942bab3aa"`, one `CREATE TABLE` for
  `user_recipe_feedback`. It is the one that hangs off upstream, and so the one a rebase re-points.
- Our revisions since, each hanging off the one before: `c3f7a91e5d20` (backfill
  `tokens_valid_after`), `d4a8b2c6e9f1` (`household_recipe_sources`), `e5b9c3d7a248` (recipe
  `total_minutes`), `f6c1d8e3b597` (`household_recipe_requests`) and `a7e2d4f9c6b3`
  (`household_calendar_sync`).
- **The fork's head is `a7e2d4f9c6b3`.** A new fork migration sets its `down_revision` to that and
  becomes the head; update this line in the same commit.
- A database is stamped with the head, not with our first revision. Where **Rollback trap** below
  says the pod fails on `b7d3a1f0c2e4`, read the head. The same trap sits between two fork images:
  going back to an image built before a fork migration needs `alembic downgrade` to that image's
  head first -- `f6c1d8e3b597` for the last image without the calendar table.
- **There must always be exactly one head.** `mealie/db/init_db.py` calls
  `command.upgrade(cfg, "head")` on every boot; with two heads that is ambiguous and the
  container never comes up.

Check it with:

```
PRODUCTION=false uv run alembic --config mealie/alembic/alembic.ini heads
```

Expected output: a single line, `a7e2d4f9c6b3 (head)` (on an untouched upstream checkout it
prints `69e942bab3aa (head)` instead). A `Secrets directory '/run/secrets' does not exist`
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

1. `git fetch origin`, then rebase `woe/scoped-feedback` onto `origin/mealie-next`.
   Basing on a release tag does not avoid the alembic collision below, it only defers it:
   PR CI tests the branch *merged into* `mealie-next`, so a tag-based branch still meets
   every migration `mealie-next` has added since that tag, and meets it as a red build
   rather than a local `heads` check. Rebase onto the branch you actually merge into.
   **This contradicts PLAN.md §7 step 1 and §3 D6**, both of which say to branch onto the new
   release tag. The contradiction is deliberate and still unresolved. If D6 is meant to win, the
   fix is a rebase onto the tag plus re-pointing `down_revision` back to that tag's head — not an
   edit to this paragraph.
2. Resolve conflicts only in the files listed under **Conflict surface**. For the seven
   generated files, take upstream wholesale and let step 5 regenerate them.
3. Re-point our migration's `down_revision` to the new upstream head. Immediately after the
   rebase, `PRODUCTION=false uv run alembic --config mealie/alembic/alembic.ini heads` will
   print *two* revisions: the fork's head (`a7e2d4f9c6b3`) and the new upstream head, because
   upstream added migrations on top of the old one and our chain still hangs off the previous
   head. The one that is not ours is the new head. Edit `down_revision` in
   `mealie/alembic/versions/*b7d3a1f0c2e4*.py` -- our first revision, the only one that points
   at upstream -- to that value. Our own revision ids never change — the database is already
   stamped with them. Update the rollback target in the **Rollback trap** section below to the
   same value in the same commit; a stale downgrade target in this file is the thing that bites
   at 2am.
4. Re-run the same `heads` command and confirm it now returns exactly one revision,
   `a7e2d4f9c6b3 (head)`. Two heads means step 3 did not take, and the container will not
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
   `alembic downgrade 69e942bab3aa`. One catch: the alembic config ships inside the installed
   package, not the working directory, so the bare CLI will not find its `.ini`. Drive it
   through alembic's API instead, which resolves the path the same way the app does at
   startup:

   ```
   kubectl -n home-automation exec deploy/mealie -- /opt/mealie/bin/python -c "
   from alembic import command
   from alembic.config import Config
   from mealie.db.init_db import ALEMBIC_DIR
   cfg = Config(str(ALEMBIC_DIR / 'alembic.ini'))
   command.downgrade(cfg, '69e942bab3aa')
   command.current(cfg)
   "
   ```

   (Adjust the namespace and workload name if the helmrelease renamed them.) That drops
   `user_recipe_feedback` and rewinds `alembic_version`; the trailing `command.current` prints
   what the database is now stamped at, and it must print `69e942bab3aa`. Only then change the
   helmrelease back to an upstream image and reconcile — and note that `v3.24.0` is no longer
   that image. `69e942bab3aa` landed on `mealie-next` on 2026-08-24, *after* the v3.24.0 tag,
   so the released image cannot locate the revision it would be handed and fails the same way
   the fork image does. Roll back to `ghcr.io/mealie-recipes/mealie:nightly` pinned by digest
   to a build at or after that commit; `nightly` moves, so resolve the digest before you need
   it, not during the incident. The feedback
   rows are gone; the stars, meal plans and recipes are not. Do this while the fork's pod is
   still up — once you have swapped the image you no longer have a container that can run the
   downgrade.

   `69e942bab3aa` is the target *while the fork is based on `mealie-next` at `14e66291`*. It
   is always whatever
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
