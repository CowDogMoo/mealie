import type { UserFeedbackOut } from "~/lib/api/types/user";

function castAt(event: UserFeedbackOut): number {
  return event.createdAt ? Date.parse(event.createdAt) : 0;
}

/**
 * "Find me a new one". Mirrors `REFILL_VOTE` in `mealie/schema/user/user_feedback.py`: logged as
 * an event so it can be undone, but it is a request rather than an opinion, so it never counts
 * as a person's current answer on a recipe. It does take the recipe out of that person's grids,
 * the way a thumbs down does: asking for a new one is saying you are done with this one for now.
 */
export const REFILL_VOTE = "refill";

/**
 * The "x" on a recipe card. Mirrors `DISMISS_VOTE` in `mealie/schema/user/user_feedback.py`: "take
 * this one off my list", with no reason and no star. Like a refill it is not an opinion, so it is
 * never a person's current answer on a recipe; like a thumbs down it takes the recipe out of that
 * person's grids, and the household's planner stops suggesting it.
 */
export const DISMISS_VOTE = "dismiss";

export function isOpinion(event: UserFeedbackOut): boolean {
  return event.vote !== REFILL_VOTE && event.vote !== DISMISS_VOTE;
}

/**
 * The newest event on each recipe among those `matches` lets through. Ties fall to the later
 * element, which is the order the API returns them in.
 */
function newestByRecipe(
  events: UserFeedbackOut[],
  matches: (event: UserFeedbackOut) => boolean,
): Map<string, UserFeedbackOut> {
  const newest = new Map<string, UserFeedbackOut>();
  for (const event of events) {
    if (!matches(event)) {
      continue;
    }
    const current = newest.get(event.recipeId);
    if (!current || castAt(event) >= castAt(current)) {
      newest.set(event.recipeId, event);
    }
  }

  return newest;
}

/**
 * The current answer for every recipe in a feedback log.
 *
 * The log keeps every vote a person ever cast; only the last one on each recipe is what they
 * think now. A refill request is not an answer, so it is skipped: asking for something new like a
 * dish you voted up must not read as having changed your mind about it. Neither is an "x".
 */
export function latestFeedbackByRecipe(events: UserFeedbackOut[]): Map<string, UserFeedbackOut> {
  return newestByRecipe(events, isOpinion);
}

/**
 * The newest refill request per recipe, so the page can show that one was asked for and undo it.
 */
export function latestRefillByRecipe(events: UserFeedbackOut[]): Map<string, UserFeedbackOut> {
  return newestByRecipe(events, event => event.vote === REFILL_VOTE);
}

/**
 * The newest "x" per recipe, so the page can list what a person removed and undo it.
 */
export function latestDismissByRecipe(events: UserFeedbackOut[]): Map<string, UserFeedbackOut> {
  return newestByRecipe(events, event => event.vote === DISMISS_VOTE);
}

/**
 * The newest event of any kind per recipe: the log read as one timeline rather than as opinions
 * and requests apart. It is what decides whether a recipe is hidden, and whether an "x" on it
 * would say anything the log does not already say.
 */
export function latestEventByRecipe(events: UserFeedbackOut[]): Map<string, UserFeedbackOut> {
  return newestByRecipe(events, () => true);
}

/**
 * The recipes a person has taken out of their own grids.
 *
 * Three presses do that: "Not for me", "Find me a new one" and the "x". Each way the recipe stays in the
 * household's collection (a housemate may love it) but drops out of this person's view until
 * they undo the event or ask to see hidden recipes. A later up or neutral vote lifts it again,
 * and so does a later thumbs down being undone -- what hides is the newest event of any kind.
 */
export function hiddenRecipeIds(events: UserFeedbackOut[]): Set<string> {
  const hidden = new Set<string>();
  for (const [recipeId, latest] of latestEventByRecipe(events)) {
    if (latest.vote === "down" || latest.vote === REFILL_VOTE || latest.vote === DISMISS_VOTE) {
      hidden.add(recipeId);
    }
  }

  return hidden;
}
