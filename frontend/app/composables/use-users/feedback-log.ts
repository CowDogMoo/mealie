import type { UserFeedbackOut } from "~/lib/api/types/user";

function castAt(event: UserFeedbackOut): number {
  return event.createdAt ? Date.parse(event.createdAt) : 0;
}

/**
 * The current answer for every recipe in a feedback log.
 *
 * The log keeps every vote a person ever cast; only the last one on each recipe is what they
 * think now. Ties fall to the later element, which is the order the API returns them in.
 */
export function latestFeedbackByRecipe(events: UserFeedbackOut[]): Map<string, UserFeedbackOut> {
  const latest = new Map<string, UserFeedbackOut>();
  for (const event of events) {
    const current = latest.get(event.recipeId);
    if (!current || castAt(event) >= castAt(current)) {
      latest.set(event.recipeId, event);
    }
  }

  return latest;
}

/**
 * The recipes a person has, as their current answer, voted down.
 *
 * "Not for me" means exactly that on the browse grids: the recipe stays in the household's
 * collection (a housemate may love it) but drops out of this person's view until they undo the
 * vote or ask to see hidden recipes. A later up or neutral vote lifts it again.
 */
export function downvotedRecipeIds(events: UserFeedbackOut[]): Set<string> {
  const hidden = new Set<string>();
  for (const [recipeId, latest] of latestFeedbackByRecipe(events)) {
    if (latest.vote === "down") {
      hidden.add(recipeId);
    }
  }

  return hidden;
}
