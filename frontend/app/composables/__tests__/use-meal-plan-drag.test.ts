import { describe, expect, test, vi } from "vitest";
import { handleMealDrop, mealDragOptions, moveEntryToDate } from "../use-meal-plan-drag";
import type { ReadPlanEntry } from "~/lib/api/types/meal-plan";

function entry(id: number, date: string, overrides: Partial<ReadPlanEntry> = {}): ReadPlanEntry {
  return {
    id,
    date,
    entryType: "dinner",
    title: "",
    text: "",
    recipeId: "recipe-id",
    groupId: "group-id",
    userId: "user-id",
    householdId: "household-id",
    ...overrides,
  };
}

// The bits of a SortableJS drop event that a meal drop reads: the dragged item and the two lists.
function drop(entryId: string | undefined, toDate: string | undefined) {
  const item = document.createElement("div");
  if (entryId !== undefined) item.dataset.entryId = entryId;
  const from = document.createElement("div");
  const to = document.createElement("div");
  if (toDate !== undefined) to.dataset.date = toDate;
  return { item, from, to };
}

describe("moveEntryToDate", () => {
  test("saves the entry with the new date", async () => {
    const update = vi.fn();
    const meal = entry(1, "2026-08-19");

    const moved = await moveEntryToDate(meal, "2026-08-21", update);

    expect(moved).toBe(true);
    expect(update).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledWith({ ...meal, date: "2026-08-21" });
  });

  test("formats a Date as the plan's yyyy-MM-dd", async () => {
    const update = vi.fn();

    await moveEntryToDate(entry(1, "2026-08-19"), new Date(2026, 7, 22), update);

    expect(update.mock.calls[0][0].date).toBe("2026-08-22");
  });

  test("does not change the entry it was given", async () => {
    const meal = entry(1, "2026-08-19");

    await moveEntryToDate(meal, "2026-08-21", vi.fn());

    expect(meal.date).toBe("2026-08-19");
  });

  test("saves nothing when the entry is already on that day", async () => {
    const update = vi.fn();

    const moved = await moveEntryToDate(entry(1, "2026-08-19"), "2026-08-19", update);

    expect(moved).toBe(false);
    expect(update).not.toHaveBeenCalled();
  });
});

describe("handleMealDrop", () => {
  const days = [
    { meals: [entry(1, "2026-08-19"), entry(2, "2026-08-19", { entryType: "lunch" })] },
    { meals: [entry(3, "2026-08-20")] },
    { meals: [] },
  ];

  test("moves the dropped meal to the date of the list it landed in", async () => {
    const update = vi.fn();

    const moved = await handleMealDrop(drop("3", "2026-08-21"), days, update);

    expect(moved).toBe(true);
    expect(update).toHaveBeenCalledWith({ ...days[1].meals[0], date: "2026-08-21" });
  });

  test("keeps everything else about the entry, including its meal type", async () => {
    const update = vi.fn();

    await handleMealDrop(drop("2", "2026-08-20"), days, update);

    expect(update).toHaveBeenCalledWith(expect.objectContaining({ id: 2, entryType: "lunch", date: "2026-08-20" }));
  });

  test("ignores a drop back onto the day it came from", async () => {
    const update = vi.fn();

    expect(await handleMealDrop(drop("1", "2026-08-19"), days, update)).toBe(false);
    expect(update).not.toHaveBeenCalled();
  });

  test.each([
    ["no date on the list", drop("1", undefined)],
    ["no entry id on the item", drop(undefined, "2026-08-20")],
    ["an entry id that is not a number", drop("abc", "2026-08-20")],
    ["an entry the plan does not have", drop("99", "2026-08-20")],
  ])("ignores a drop with %s", async (_name, evt) => {
    const update = vi.fn();

    expect(await handleMealDrop(evt, days, update)).toBe(false);
    expect(update).not.toHaveBeenCalled();
  });
});

describe("mealDragOptions", () => {
  test("lets meals move between day lists, and only picks one up after a press on touch screens", () => {
    expect(mealDragOptions.group).toBe("meals");
    expect(mealDragOptions.delayOnTouchOnly).toBe(true);
    expect(mealDragOptions.delay).toBeGreaterThan(0);
  });
});
