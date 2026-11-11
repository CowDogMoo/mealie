import { beforeEach, describe, expect, test, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createVuetify } from "vuetify";
import { VAlert, VBtn, VChip, VIcon, VSpacer } from "vuetify/components";
import type { PantryLeftOffItem, ShoppingListOut } from "~/lib/api/types/household";

const { leftOff, createOne, alertSuccess, alertError } = vi.hoisted(() => ({
  leftOff: vi.fn(),
  createOne: vi.fn(),
  alertSuccess: vi.fn(),
  alertError: vi.fn(),
}));

vi.mock("~/composables/use-toast", () => ({
  alert: { success: alertSuccess, error: alertError, info: vi.fn() },
}));

vi.mock("~/composables/api", () => ({
  useUserApi: () => ({ pantry: { leftOff }, shopping: { items: { createOne } } }),
}));

// Nuxt auto-imports the vitest config does not provide; the component reads them as globals
const icons = new Proxy({}, { get: (_target, name) => String(name) });
vi.stubGlobal("useNuxtApp", () => ({ $globals: { icons } }));

const LeftOff = (await import("../ShoppingListPantryLeftOff.vue")).default;

function staple(id: string, name: string, overrides: Partial<PantryLeftOffItem> = {}): PantryLeftOffItem {
  return {
    food: { id, name },
    recipes: [{ id: "r1", slug: "lentil-tacos", name: "Lentil Tacos" }],
    onList: false,
    ...overrides,
  };
}

function listWith(recipeIds: string[] = ["r1"], foodIds: string[] = ["lentils"]) {
  return {
    id: "list-1",
    name: "Groceries",
    recipeReferences: recipeIds.map(recipeId => ({ recipeId, recipeQuantity: 1 })),
    listItems: foodIds.map((foodId, i) => ({ id: `i${i}`, foodId, checked: false })),
  };
}

// the fixtures carry only the fields the component reads; the cast keeps them readable
function mountLeftOff(modelValue: ReturnType<typeof listWith> | null, props: Record<string, unknown> = {}) {
  return mount(LeftOff, {
    props: { modelValue: modelValue as unknown as ShoppingListOut | null, ...props },
    global: {
      plugins: [createVuetify({ components: { VAlert, VBtn, VChip, VIcon, VSpacer } })],
      mocks: { $globals: { icons } },
      stubs: { RouterLink: true },
    },
  });
}

const chips = (wrapper: ReturnType<typeof mountLeftOff>) => wrapper.findAll("[data-test=\"pantry-left-off-add\"]");

beforeEach(() => {
  vi.clearAllMocks();
  leftOff.mockResolvedValue({ data: { items: [staple("salt", "salt"), staple("oil", "olive oil")] }, error: null });
  createOne.mockResolvedValue({ data: { createdItems: [{ id: "new" }] }, error: null });
});

describe("what the list shows", () => {
  test("names each staple the list's recipes use and it left off", async () => {
    const wrapper = mountLeftOff(listWith());
    await flushPromises();

    expect(leftOff).toHaveBeenCalledWith("list-1");
    expect(wrapper.find("[data-test=\"pantry-left-off\"]").text()).toContain("Left off because you usually have it");
    expect(chips(wrapper).map(chip => chip.text())).toEqual(["salt", "olive oil"]);
    expect(chips(wrapper)[0]!.attributes("title")).toBe("Used by Lentil Tacos");
  });

  test("shows nothing when nothing was left off", async () => {
    leftOff.mockResolvedValue({ data: { items: [] }, error: null });
    const wrapper = mountLeftOff(listWith());
    await flushPromises();

    expect(wrapper.find("[data-test=\"pantry-left-off\"]").exists()).toBe(false);
  });

  test("a staple somebody already put on the list is not offered again", async () => {
    leftOff.mockResolvedValue({
      data: { items: [staple("salt", "salt", { onList: true }), staple("oil", "olive oil")] },
      error: null,
    });
    const wrapper = mountLeftOff(listWith());
    await flushPromises();

    expect(chips(wrapper).map(chip => chip.text())).toEqual(["olive oil"]);
  });

  test("a list with no recipes on it is never asked about", async () => {
    const wrapper = mountLeftOff(listWith([]));
    await flushPromises();

    expect(leftOff).not.toHaveBeenCalled();
    expect(wrapper.find("[data-test=\"pantry-left-off\"]").exists()).toBe(false);
  });

  test("a failed read keeps what is on screen", async () => {
    const wrapper = mountLeftOff(listWith());
    await flushPromises();

    leftOff.mockResolvedValue({ data: null, error: new Error("offline") });
    await wrapper.setProps({ modelValue: listWith(["r1", "r2"]) as unknown as ShoppingListOut });
    await flushPromises();

    expect(chips(wrapper)).toHaveLength(2);
  });
});

describe("when it asks again", () => {
  test("re-reads when the list's recipes or foods change", async () => {
    const wrapper = mountLeftOff(listWith());
    await flushPromises();
    expect(leftOff).toHaveBeenCalledTimes(1);

    await wrapper.setProps({ modelValue: listWith(["r1", "r2"]) as unknown as ShoppingListOut });
    await flushPromises();
    expect(leftOff).toHaveBeenCalledTimes(2);

    await wrapper.setProps({ modelValue: listWith(["r1", "r2"], ["lentils", "salt"]) as unknown as ShoppingListOut });
    await flushPromises();
    expect(leftOff).toHaveBeenCalledTimes(3);
  });

  test("does not re-read when the page refreshes an unchanged list", async () => {
    const wrapper = mountLeftOff(listWith());
    await flushPromises();

    // the page polls: a new object with the same recipes and foods, e.g. after a box is ticked
    await wrapper.setProps({ modelValue: listWith() as unknown as ShoppingListOut });
    await flushPromises();

    expect(leftOff).toHaveBeenCalledTimes(1);
  });
});

describe("putting one back", () => {
  test("a tap adds the food as an ordinary unticked item and tells the page", async () => {
    const wrapper = mountLeftOff(listWith());
    await flushPromises();

    await chips(wrapper)[0]!.trigger("click");
    await flushPromises();

    expect(createOne).toHaveBeenCalledWith({
      shoppingListId: "list-1",
      foodId: "salt",
      quantity: 0,
      checked: false,
      position: 1,
    });
    expect(alertSuccess).toHaveBeenCalledWith("salt added to the list");
    expect(wrapper.emitted("changed")).toHaveLength(1);
    expect(chips(wrapper).map(chip => chip.text())).toEqual(["olive oil"]);
  });

  test("a failed add says so and leaves the staple offered", async () => {
    createOne.mockResolvedValue({ data: null, error: new Error("nope") });
    const wrapper = mountLeftOff(listWith());
    await flushPromises();

    await chips(wrapper)[0]!.trigger("click");
    await flushPromises();

    expect(alertError).toHaveBeenCalled();
    expect(wrapper.emitted("changed")).toBeUndefined();
    expect(chips(wrapper)).toHaveLength(2);
  });

  test("nothing can be added while the list is offline", async () => {
    const wrapper = mountLeftOff(listWith(), { disabled: true });
    await flushPromises();

    await chips(wrapper)[0]!.trigger("click");
    await flushPromises();

    expect(createOne).not.toHaveBeenCalled();
  });
});
