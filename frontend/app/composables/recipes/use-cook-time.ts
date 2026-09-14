/**
 * How long a recipe takes, and what the household thinks of that length.
 *
 * The thresholds are the meal planner's, to the minute: under 45 minutes is a
 * weeknight dinner, 45 to 60 inclusive is the band that needs somebody to say
 * yes, and past 60 is not a weeknight dinner at all. They match the `45-60` tag
 * the planner applies, so a recipe cannot read as fine here and as borderline
 * there.
 *
 * Everything reads `totalMinutes`, which the server parses from the free-text
 * cook time on write. Nothing here parses a string: a badge that did its own
 * parsing would eventually disagree with the filter, and then the grid would be
 * showing one thing and querying another.
 */

export const BAND_MIN_MINUTES = 45;
export const BAND_MAX_MINUTES = 60;

export type CookTimeLevel = "weeknight" | "band" | "long" | "unknown";

/** The four things a cook-time filter can ask for. `any` is no filter at all. */
export type CookTimeChoice = "any" | "weeknight" | "band" | "long";

export function cookTimeLevel(minutes?: number | null): CookTimeLevel {
  if (typeof minutes !== "number" || !Number.isFinite(minutes)) {
    return "unknown";
  }
  if (minutes < BAND_MIN_MINUTES) {
    return "weeknight";
  }
  if (minutes <= BAND_MAX_MINUTES) {
    return "band";
  }
  return "long";
}

/**
 * The server-side filter for a choice, in Mealie's query language.
 *
 * `any` is null rather than a clause that matches everything, so the caller can
 * tell "no filter" from "a filter that happens to match everything" and leave
 * the query untouched.
 *
 * A recipe whose cook time the server could not read has no `totalMinutes` and
 * so belongs to none of these. That is deliberate — it is not secretly short —
 * and it is why the grid still offers an unfiltered view.
 */
export function cookTimeQueryFilter(choice: CookTimeChoice): string | null {
  switch (choice) {
    case "weeknight":
      return `totalMinutes < ${BAND_MIN_MINUTES}`;
    case "band":
      return `totalMinutes >= ${BAND_MIN_MINUTES} AND totalMinutes <= ${BAND_MAX_MINUTES}`;
    case "long":
      return `totalMinutes > ${BAND_MAX_MINUTES}`;
    default:
      return null;
  }
}

/**
 * Join the page's own filter to the cook-time one without either swallowing the
 * other. Each side is parenthesised because Mealie's `AND` binds tighter than
 * `OR`, so an unbracketed `a OR b` on either side would silently change meaning.
 */
export function combineQueryFilters(...filters: (string | null | undefined)[]): string | null {
  const present = filters.filter((f): f is string => !!f && f.trim().length > 0);
  if (!present.length) {
    return null;
  }
  if (present.length === 1) {
    return present[0]!;
  }
  return present.map(f => `(${f})`).join(" AND ");
}

/** "40 min", "1 hr", "1 hr 15 min" — short enough to sit on a card corner. */
export function formatCookTime(minutes?: number | null): string {
  if (typeof minutes !== "number" || !Number.isFinite(minutes)) {
    return "";
  }
  const whole = Math.max(0, Math.round(minutes));
  const hours = Math.floor(whole / 60);
  const rest = whole % 60;

  if (!hours) {
    return `${rest} min`;
  }
  return rest ? `${hours} hr ${rest} min` : `${hours} hr`;
}
