import { describe, expect, test, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import PlannerView from "../view.vue";
import type { ReadPlanEntry } from "~/lib/api/types/meal-plan";

// SortableJS needs a real browser to run a drag, so the list is stood in for: it renders the
// day's meals and lets a test fire the same `end` event Sortable fires when a meal is dropped.
// vi.mock is hoisted above the imports, so the stand-in has to be built by vi.hoisted too
const Draggable = vi.hoisted(() => ({
  name: "VueDraggable",
  inheritAttrs: false,
  emits: ["end"],
  setup(_props: unknown, { attrs, slots, emit }: any) {
    return () =>
      h("div", { ...attrs, "data-test": "day", "onDrop": (evt: Event) => emit("end", (evt as CustomEvent).detail) }, slots.default?.());
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

function mountView(mealplans: { date: Date; meals: ReadPlanEntry[] }[]) {
  const updateOne = vi.fn();
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

function dropOn(wrapper: ReturnType<typeof mountView>["wrapper"], from: number, to: number, entryId: number) {
  const days = wrapper.findAll("[data-test=day]");
  const item = days[from].find(`[data-entry-id="${entryId}"]`).element as HTMLElement;
  days[to].element.dispatchEvent(
    new CustomEvent("drop", { detail: { item, from: days[from].element, to: days[to].element } }),
  );
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

    dropOn(wrapper, 0, 2, 1);
    await flushPromises();

    expect(updateOne).toHaveBeenCalledTimes(1);
    expect(updateOne).toHaveBeenCalledWith({ ...tacos, date: "2026-08-19" });
  });

  test("dropping a meal back on its own day saves nothing", async () => {
    const { wrapper, updateOne } = mountView([
      { date: monday, meals: [meal(1, "2026-08-17", "Tacos")] },
      { date: tuesday, meals: [] },
    ]);

    dropOn(wrapper, 0, 0, 1);
    await flushPromises();

    expect(updateOne).not.toHaveBeenCalled();
  });
});
