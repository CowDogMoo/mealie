import { describe, expect, test, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import PlannerView from "../view.vue";
import type { ReadPlanEntry } from "~/lib/api/types/meal-plan";

// SortableJS needs a real browser to run a drag, so the list is stood in for: it renders the
// day's meals, holds the day's list as its v-model, and lets a test emit what the real list emits
// when a meal is dropped.
// vi.mock is hoisted above the imports, so the stand-in has to be built by vi.hoisted too
const Draggable = vi.hoisted(() => ({
  name: "VueDraggable",
  inheritAttrs: false,
  props: ["modelValue"],
  emits: ["end", "update:modelValue"],
  setup(_props: unknown, { attrs, slots }: any) {
    return () => h("div", { ...attrs, "data-test": "day" }, slots.default?.());
  },
}));

vi.mock("vue-draggable-plus", () => ({ VueDraggable: Draggable }));

const Card = defineComponent({
  name: "RecipeCardMobile",
  props: { name: { type: String, default: "" } },
  setup: props => () => h("span", { "data-test": "card" }, props.name),
});

function meal(id: number, date: string, title: string, entryType: ReadPlanEntry["entryType"] = "dinner"): ReadPlanEntry {
  return { id, date, entryType, title, text: "", recipeId: null, groupId: "g", userId: "u", householdId: "h" };
}

function mountView(
  mealplans: { date: Date; meals: ReadPlanEntry[] }[],
  updateOne = vi.fn((_entry: ReadPlanEntry) => Promise.resolve(true)),
) {
  const wrapper = mount(PlannerView, {
    props: { mealplans, actions: { updateOne } as never },
    global: {
      stubs: {
        VContainer: { template: "<div><slot /></div>" },
        VRow: { template: "<div><slot /></div>" },
        VCol: { template: "<div><slot /></div>" },
        VCard: { template: "<div><slot /></div>" },
        GroupMealPlanDayContextMenu: true,
        RecipeCardMobile: Card,
      },
    },
  });
  return { wrapper, updateOne };
}

type View = ReturnType<typeof mountView>["wrapper"];

// What the real list does when a meal is dropped: a meal that lands on another day is taken out
// of the list it was dragged from and put in the list it landed in (here always at the end, as if
// dropped below the day's last meal), and then the list it came from emits `end`.
async function dropOn(wrapper: View, from: number, to: number, entryId: number) {
  const days = wrapper.findAllComponents(Draggable);
  const item = days[from].find(`[data-entry-id="${entryId}"]`).element as HTMLElement;
  if (from !== to) {
    const source: ReadPlanEntry[] = days[from].props("modelValue");
    const target: ReadPlanEntry[] = days[to].props("modelValue");
    days[from].vm.$emit("update:modelValue", source.filter(meal => meal.id !== entryId));
    days[to].vm.$emit("update:modelValue", [...target, ...source.filter(meal => meal.id === entryId)]);
  }
  days[from].vm.$emit("end", { item, from: days[from].element, to: days[to].element });
  await flushPromises();
}

function entryIdsByDay(wrapper: View) {
  return wrapper
    .findAll("[data-test=day]")
    .map(day => day.findAll("[data-entry-id]").map(item => item.attributes("data-entry-id")));
}

function pendingSave() {
  let finish!: (saved: boolean) => void;
  const updateOne = vi.fn((_entry: ReadPlanEntry) => new Promise<boolean>((resolve) => {
    finish = resolve;
  }));
  return { updateOne, finish: (saved: boolean) => finish(saved) };
}

describe("planner view drag and drop", () => {
  const monday = new Date(2026, 7, 17);
  const tuesday = new Date(2026, 7, 18);
  const wednesday = new Date(2026, 7, 19);

  test("each day is a drop zone carrying its date", () => {
    const { wrapper } = mountView([
      { date: monday, meals: [] },
      { date: tuesday, meals: [] },
    ]);

    expect(wrapper.findAll("[data-test=day]").map(day => day.attributes("data-date"))).toEqual([
      "2026-08-17",
      "2026-08-18",
    ]);
  });

  test("lists a day's meals, one tagged with its entry id", () => {
    const { wrapper } = mountView([
      { date: monday, meals: [meal(1, "2026-08-17", "Tacos"), meal(2, "2026-08-17", "Soup", "lunch")] },
      { date: tuesday, meals: [] },
    ]);

    const day = wrapper.findAll("[data-test=day]")[0];
    // lunch comes before dinner, whatever order the API returned them in
    expect(day.findAll("[data-entry-id]").map(item => item.attributes("data-entry-id"))).toEqual(["2", "1"]);
  });

  test("dropping a meal on another day saves it with that day's date", async () => {
    const tacos = meal(1, "2026-08-17", "Tacos");
    const { wrapper, updateOne } = mountView([
      { date: monday, meals: [tacos] },
      { date: tuesday, meals: [] },
      { date: wednesday, meals: [] },
    ]);

    await dropOn(wrapper, 0, 2, 1);

    expect(updateOne).toHaveBeenCalledTimes(1);
    expect(updateOne).toHaveBeenCalledWith({ ...tacos, date: "2026-08-19" });
  });

  test("dropping a meal back on its own day saves nothing", async () => {
    const { wrapper, updateOne } = mountView([
      { date: monday, meals: [meal(1, "2026-08-17", "Tacos")] },
      { date: tuesday, meals: [] },
    ]);

    await dropOn(wrapper, 0, 0, 1);

    expect(updateOne).not.toHaveBeenCalled();
    expect(entryIdsByDay(wrapper)).toEqual([["1"], []]);
  });

  test("a dropped meal takes its place in the day's meal-type order", async () => {
    const { wrapper } = mountView([
      { date: monday, meals: [meal(1, "2026-08-17", "Tacos")] },
      { date: tuesday, meals: [meal(2, "2026-08-18", "Chili"), meal(3, "2026-08-18", "Rice", "side")] },
    ]);

    await dropOn(wrapper, 0, 1, 1);

    expect(entryIdsByDay(wrapper)).toEqual([[], ["2", "1", "3"]]);
    const headings = wrapper.findAll("[data-test=day]")[1].findAll("p").map(heading => heading.text());
    expect(headings).toEqual(["Dinner", "Side"]);
  });

  test("a saved meal stays on the day it was dropped on", async () => {
    const save = pendingSave();
    const { wrapper } = mountView(
      [
        { date: monday, meals: [meal(1, "2026-08-17", "Tacos")] },
        { date: tuesday, meals: [] },
      ],
      save.updateOne,
    );

    await dropOn(wrapper, 0, 1, 1);
    expect(entryIdsByDay(wrapper)).toEqual([[], ["1"]]);

    save.finish(true);
    await flushPromises();

    expect(entryIdsByDay(wrapper)).toEqual([[], ["1"]]);
  });

  test("a meal whose save fails goes back to the day it was dragged from", async () => {
    const save = pendingSave();
    const { wrapper } = mountView(
      [
        { date: monday, meals: [meal(1, "2026-08-17", "Tacos")] },
        { date: tuesday, meals: [] },
      ],
      save.updateOne,
    );

    await dropOn(wrapper, 0, 1, 1);
    expect(entryIdsByDay(wrapper)).toEqual([[], ["1"]]);

    save.finish(false);
    await flushPromises();

    expect(entryIdsByDay(wrapper)).toEqual([["1"], []]);
  });

  test("dragging a meal back before its first move is saved saves the move back too", async () => {
    const tacos = meal(1, "2026-08-17", "Tacos");
    const { wrapper, updateOne } = mountView(
      [
        { date: monday, meals: [tacos] },
        { date: tuesday, meals: [] },
      ],
      pendingSave().updateOne,
    );

    await dropOn(wrapper, 0, 1, 1);
    await dropOn(wrapper, 1, 0, 1);

    expect(updateOne).toHaveBeenCalledTimes(2);
    expect(updateOne).toHaveBeenLastCalledWith({ ...tacos, date: "2026-08-17" });
  });
});
