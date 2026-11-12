import type { ReadPlanEntry, UpdatePlanEntry } from "~/lib/api/types/meal-plan";

/**
 * Options for the meal planner's day lists, which let entries be dragged between days.
 *
 * On a touch screen a drag only starts after a short press (a quarter second), so a finger that is
 * scrolling the page never picks a meal up by accident; with a mouse the drag starts at once.
 * `touchStartThreshold` lets a finger wobble a few pixels during that press without cancelling it.
 */
export const mealDragOptions = {
  group: "meals",
  delay: 250,
  delayOnTouchOnly: true,
  touchStartThreshold: 8,
  animation: 150,
  ghostClass: "meal-drag-ghost",
} as const;

/** Saves an entry and resolves to whether the save landed. */
type UpdateEntry = (entry: UpdatePlanEntry) => Promise<boolean>;

/**
 * Give `entry` a new date (yyyy-MM-dd) and save it, resolving to whether the save landed. The
 * calendar sync listens for entry updates, so it pushes the move on its own.
 */
export function moveEntryToDate(entry: ReadPlanEntry, date: string, update: UpdateEntry): Promise<boolean> {
  // Send a copy: the entry object is shared with the plan the page was given.
  return update({ ...entry, date });
}

/** The part of a SortableJS drop event that a meal drop needs. */
export interface MealDropEvent {
  item: HTMLElement;
  from: HTMLElement;
  to: HTMLElement;
}

/**
 * Handle a meal being dropped on a day. Each day's list carries its date as `data-date`
 * (yyyy-MM-dd) and each meal carries its entry id as `data-entry-id`.
 *
 * A drop on the list the meal was picked up from moves nothing. A drop on any other list is saved
 * whatever date the plan has for the entry, because the plan lags behind while an earlier move is
 * still being saved. Resolves to false when the meal landed on another day and that move was not
 * saved, which leaves the caller's lists showing a move the plan does not have.
 */
export async function handleMealDrop(
  evt: MealDropEvent,
  days: { meals: ReadPlanEntry[] }[],
  update: UpdateEntry,
): Promise<boolean> {
  if (evt.from === evt.to) {
    return true;
  }

  const date = evt.to.dataset.date;
  const id = Number(evt.item.dataset.entryId);
  if (!date || Number.isNaN(id)) {
    return false;
  }

  const entry = days.flatMap(day => day.meals).find(meal => meal.id === id);
  if (!entry) {
    return false;
  }

  return moveEntryToDate(entry, date, update);
}
