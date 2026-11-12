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
    const update = vi.fn().mockResolvedValue(true);
    const meal = entry(1, "2026-08-19");

    const saved = await moveEntryToDate(meal, "2026-08-21", update);

    expect(saved).toBe(true);
    expect(update).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledWith({ ...meal, date: "2026-08-21" });
  });

  test("does not change the entry it was given", async () => {
    const meal = entry(1, "2026-08-19");

    await moveEntryToDate(meal, "2026-08-21", vi.fn().mockResolvedValue(true));

    expect(meal.date).toBe("2026-08-19");
  });

  test("reports a save that did not land", async () => {
    const update = vi.fn().mockResolvedValue(false);

    expect(await moveEntryToDate(entry(1, "2026-08-19"), "2026-08-21", update)).toBe(false);
  });
});

describe("handleMealDrop", () => {
  const days = [
    { meals: [entry(1, "2026-08-19"), entry(2, "2026-08-19", { entryType: "lunch" })] },
    { meals: [entry(3, "2026-08-20")] },
    { meals: [] },
  ];

  test("moves the dropped meal to the date of the list it landed in", async () => {
    const update = vi.fn().mockResolvedValue(true);

    const saved = await handleMealDrop(drop("3", "2026-08-21"), days, update);

    expect(saved).toBe(true);
    expect(update).toHaveBeenCalledWith({ ...days[1].meals[0], date: "2026-08-21" });
  });

  test("keeps everything else about the entry, including its meal type", async () => {
    const update = vi.fn().mockResolvedValue(true);

    await handleMealDrop(drop("2", "2026-08-20"), days, update);

    expect(update).toHaveBeenCalledWith(expect.objectContaining({ id: 2, entryType: "lunch", date: "2026-08-20" }));
  });

  test("saves nothing for a drop on the list the meal was picked up from", async () => {
    const update = vi.fn().mockResolvedValue(true);
    const evt = drop("1", "2026-08-20");

    expect(await handleMealDrop({ ...evt, from: evt.to }, days, update)).toBe(true);
    expect(update).not.toHaveBeenCalled();
  });

  // While an earlier move is still being saved the plan has the entry on its old day, so a drag
  // back to that day lands on another list but on the date the plan already shows.
  test("saves a drop on another list even when the plan has the entry on that date", async () => {
    const update = vi.fn().mockResolvedValue(true);

    const saved = await handleMealDrop(drop("1", "2026-08-19"), days, update);

    expect(saved).toBe(true);
    expect(update).toHaveBeenCalledWith({ ...days[0].meals[0], date: "2026-08-19" });
  });

  test("reports a move that was not saved", async () => {
    const update = vi.fn().mockResolvedValue(false);

    expect(await handleMealDrop(drop("3", "2026-08-21"), days, update)).toBe(false);
  });

  test.each([
    ["no date on the list", drop("1", undefined)],
    ["no entry id on the item", drop(undefined, "2026-08-20")],
    ["an entry id that is not a number", drop("abc", "2026-08-20")],
    ["an entry the plan does not have", drop("99", "2026-08-20")],
  ])("saves nothing, and reports the move as not saved, for a drop with %s", async (_name, evt) => {
    const update = vi.fn().mockResolvedValue(true);

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
