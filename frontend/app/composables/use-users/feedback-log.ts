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

export function isOpinion(event: UserFeedbackOut): boolean {
  return event.vote !== REFILL_VOTE;
}

/**
 * The current answer for every recipe in a feedback log.
 *
 * The log keeps every vote a person ever cast; only the last one on each recipe is what they
 * think now. Ties fall to the later element, which is the order the API returns them in. A refill
 * request is not an answer, so it is skipped: asking for something new like a dish you voted up
 * must not read as having changed your mind about it.
 */
export function latestFeedbackByRecipe(events: UserFeedbackOut[]): Map<string, UserFeedbackOut> {
  const latest = new Map<string, UserFeedbackOut>();
  for (const event of events) {
    if (!isOpinion(event)) {
      continue;
    }
    const current = latest.get(event.recipeId);
    if (!current || castAt(event) >= castAt(current)) {
      latest.set(event.recipeId, event);
    }
  }

  return latest;
}

/**
 * The newest refill request per recipe, so the page can show that one was asked for and undo it.
 */
export function latestRefillByRecipe(events: UserFeedbackOut[]): Map<string, UserFeedbackOut> {
  const latest = new Map<string, UserFeedbackOut>();
  for (const event of events) {
    if (isOpinion(event)) {
      continue;
    }
    const current = latest.get(event.recipeId);
    if (!current || castAt(event) >= castAt(current)) {
      latest.set(event.recipeId, event);
    }
  }

  return latest;
}

/**
 * The recipes a person has taken out of their own grids.
 *
 * Two presses do that: "Not for me" and "Find me a new one". Either way the recipe stays in the
 * household's collection (a housemate may love it) but drops out of this person's view until
 * they undo the event or ask to see hidden recipes. A later up or neutral vote lifts it again,
 * and so does a later thumbs down being undone -- what hides is the newest event of any kind,
 * so the log is read as one timeline here rather than as opinions and requests apart.
 */
export function hiddenRecipeIds(events: UserFeedbackOut[]): Set<string> {
  const newest = new Map<string, UserFeedbackOut>();
  for (const event of events) {
    const current = newest.get(event.recipeId);
    if (!current || castAt(event) >= castAt(current)) {
      newest.set(event.recipeId, event);
    }
  }

  const hidden = new Set<string>();
  for (const [recipeId, latest] of newest) {
    if (latest.vote === "down" || latest.vote === REFILL_VOTE) {
      hidden.add(recipeId);
    }
  }

  return hidden;
}
