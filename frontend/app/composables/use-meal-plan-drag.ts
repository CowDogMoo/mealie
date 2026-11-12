import { format } from "date-fns";
import type { ReadPlanEntry, UpdatePlanEntry } from "~/lib/api/types/meal-plan";

/**
 * Options shared by every meal plan list that lets entries be dragged between days.
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
  chosenClass: "meal-drag-chosen",
} as const;

type UpdateEntry = (entry: UpdatePlanEntry) => Promise<unknown> | unknown;

/**
 * Give `entry` a new date and save it. Returns false, and saves nothing, when the entry already
 * sits on that day. The calendar sync listens for entry updates, so it pushes the move on its own.
 */
export async function moveEntryToDate(entry: ReadPlanEntry, date: Date | string, update: UpdateEntry): Promise<boolean> {
  const newDate = typeof date === "string" ? date : format(date, "yyyy-MM-dd");
  if (entry.date === newDate) {
    return false;
  }

  // Send a copy: the entry object is shared with the plan the page was given.
  await update({ ...entry, date: newDate });
  return true;
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
 */
export async function handleMealDrop(
  evt: MealDropEvent,
  days: { meals: ReadPlanEntry[] }[],
  update: UpdateEntry,
): Promise<boolean> {
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
