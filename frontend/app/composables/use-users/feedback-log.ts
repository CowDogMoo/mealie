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
