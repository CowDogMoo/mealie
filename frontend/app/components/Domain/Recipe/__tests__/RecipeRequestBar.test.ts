import { beforeEach, describe, expect, test, vi } from "vitest";
import { RouterLinkStub, flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { createVuetify } from "vuetify";
import { VAlert, VBtn, VCardText, VIcon, VTextField } from "vuetify/components";
import type { RecipeRequestOut } from "~/lib/api/types/household";

const { getAll, create, clear, alertSuccess, alertError } = vi.hoisted(() => ({
  getAll: vi.fn(),
  create: vi.fn(),
  clear: vi.fn(),
  alertSuccess: vi.fn(),
  alertError: vi.fn(),
}));

vi.mock("~/composables/use-toast", () => ({
  alert: { success: alertSuccess, error: alertError, info: vi.fn() },
}));

vi.mock("~/composables/api", () => ({
  useUserApi: () => ({ recipeRequests: { getAll, create, clear } }),
}));

// Nuxt auto-imports the vitest config does not provide; the component reads them as globals
const icons = new Proxy({}, { get: (_target, name) => String(name) });
vi.stubGlobal("useNuxtApp", () => ({ $globals: { icons } }));

// the button's loading spinner is a VProgressCircular, which measures itself with a ResizeObserver
// jsdom does not implement
vi.stubGlobal("ResizeObserver", class {
  observe() {}
  unobserve() {}
  disconnect() {}
});

const RecipeRequestBar = (await import("../RecipeRequestBar.vue")).default;

// BaseDialog is a global component in the app; this stub renders its slot and exposes the submit
// action, because what matters is that pressing the button asks before it posts.
const BaseDialog = defineComponent({
  name: "BaseDialog",
  props: { modelValue: Boolean, submitText: { type: String, default: "" } },
  emits: ["update:modelValue", "submit"],
  setup: (props, { slots, emit }) => () =>
    props.modelValue
      ? h("div", { "data-test": "request-dialog" }, [
          slots.default?.(),
          h("button", { "data-test": "request-submit", "onClick": () => emit("submit") }, props.submitText),
        ])
      : null,
});

function request(overrides: Partial<RecipeRequestOut> = {}): RecipeRequestOut {
  return {
    id: "req-1",
    groupId: "g",
    householdId: "h",
    status: "pending",
    requestedByName: "Amanda",
    note: null,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    ...overrides,
  };
}

function serve(requests: RecipeRequestOut[]) {
  getAll.mockResolvedValue({ data: { requests }, error: null });
}

async function mountBar() {
  const wrapper = mount(RecipeRequestBar, {
    props: { groupSlug: "home" },
    global: {
      plugins: [createVuetify({ components: { VAlert, VBtn, VCardText, VIcon, VTextField } })],
      components: { BaseDialog },
      stubs: { RouterLink: RouterLinkStub },
      mocks: { $globals: { icons } },
    },
  });
  await flushPromises();
  return wrapper;
}

const statuses = (wrapper: Awaited<ReturnType<typeof mountBar>>) => wrapper.findAll("[data-test=\"recipe-request-status\"]");

beforeEach(() => {
  vi.clearAllMocks();
  serve([]);
  create.mockResolvedValue({ data: request(), error: null });
  clear.mockResolvedValue({ data: null, error: null });
});

describe("asking", () => {
  test("the bar reads the household's requests when it mounts", async () => {
    await mountBar();
    expect(getAll).toHaveBeenCalledTimes(1);
  });

  test("pressing the button opens the dialog and posts nothing by itself", async () => {
    const wrapper = await mountBar();

    await wrapper.find("[data-test=\"recipe-request-ask\"]").trigger("click");

    expect(create).not.toHaveBeenCalled();
    const dialog = wrapper.find("[data-test=\"request-dialog\"]").text();
    expect(dialog).toContain("Nothing is hidden and nothing on the meal plan changes");
  });

  test("submitting posts the note and says a recipe is on the way", async () => {
    const wrapper = await mountBar();

    await wrapper.find("[data-test=\"recipe-request-ask\"]").trigger("click");
    await wrapper.find("[data-test=\"recipe-request-note\"] input").setValue("  something with lentils  ");
    await wrapper.find("[data-test=\"request-submit\"]").trigger("click");
    await flushPromises();

    expect(create).toHaveBeenCalledWith({ note: "something with lentils" });
    expect(alertSuccess).toHaveBeenCalledWith("Got it. A new recipe will show up under Recipes in a few minutes.");
    // the queue is re-read so the new request shows up straight away
    expect(getAll).toHaveBeenCalledTimes(2);
  });

  test("an empty note is sent as no note at all", async () => {
    const wrapper = await mountBar();

    await wrapper.find("[data-test=\"recipe-request-ask\"]").trigger("click");
    await wrapper.find("[data-test=\"request-submit\"]").trigger("click");
    await flushPromises();

    expect(create).toHaveBeenCalledWith({ note: null });
  });

  test("a household at its cap is told so, and no success is claimed", async () => {
    create.mockResolvedValue({ data: null, error: { response: { status: 409 } } });
    const wrapper = await mountBar();

    await wrapper.find("[data-test=\"recipe-request-ask\"]").trigger("click");
    await wrapper.find("[data-test=\"request-submit\"]").trigger("click");
    await flushPromises();

    expect(alertError).toHaveBeenCalledWith("A few requests are already waiting. Give them a few minutes, or cancel one.");
    expect(alertSuccess).not.toHaveBeenCalled();
  });

  test("submitting twice while the first is in flight posts once", async () => {
    let release: (value: unknown) => void = () => {};
    create.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const wrapper = await mountBar();

    await wrapper.find("[data-test=\"recipe-request-ask\"]").trigger("click");
    await wrapper.find("[data-test=\"request-submit\"]").trigger("click");
    await wrapper.find("[data-test=\"request-submit\"]").trigger("click");
    release({ data: request(), error: null });
    await flushPromises();

    expect(create).toHaveBeenCalledTimes(1);
    expect(alertSuccess).toHaveBeenCalledTimes(1);
  });
});

describe("what the bar shows once a request exists", () => {
  test("a waiting request names who asked and what for, and can be cancelled", async () => {
    serve([request({ note: "something with lentils" })]);
    const wrapper = await mountBar();

    const [status] = statuses(wrapper);
    expect(status!.text()).toContain("Amanda asked for a new recipe");
    expect(status!.text()).toContain("something with lentils");

    await status!.find("[data-test=\"recipe-request-clear\"]").trigger("click");
    await flushPromises();
    expect(clear).toHaveBeenCalledWith("req-1");
  });

  test("a running search says so and offers no cancel", async () => {
    serve([request({ status: "filling" })]);
    const wrapper = await mountBar();

    const [status] = statuses(wrapper);
    expect(status!.text()).toContain("Looking for a new recipe for Amanda");
    expect(status!.find("[data-test=\"recipe-request-clear\"]").exists()).toBe(false);
  });

  test("a filled request links to the recipe it brought in and can be dismissed", async () => {
    serve([request({ status: "filled", recipeSlug: "lentil-shepherds-pie", recipeName: "Lentil Shepherd's Pie" })]);
    const wrapper = await mountBar();

    const link = wrapper.findComponent(RouterLinkStub);
    expect(link.props("to")).toBe("/g/home/r/lentil-shepherds-pie");
    expect(link.text()).toBe("Lentil Shepherd's Pie");
    expect(statuses(wrapper)[0]!.find("[data-test=\"recipe-request-clear\"]").text()).toBe("Dismiss");
  });

  test("a failed request says why", async () => {
    serve([request({ status: "failed", result: "every pick was already in the recipe box" })]);
    const wrapper = await mountBar();

    const [status] = statuses(wrapper);
    expect(status!.text()).toContain("No new recipe this time");
    expect(status!.text()).toContain("every pick was already in the recipe box");
  });

  test("cancelling after the search started explains why it cannot", async () => {
    serve([request()]);
    clear.mockResolvedValue({ data: null, error: { response: { status: 409 } } });
    const wrapper = await mountBar();

    await statuses(wrapper)[0]!.find("[data-test=\"recipe-request-clear\"]").trigger("click");
    await flushPromises();

    expect(alertError).toHaveBeenCalledWith(
      "The search has already started, so it can't be cancelled. You can dismiss it once it finishes.",
    );
  });

  test("a request somebody else already cleared is not an error", async () => {
    serve([request()]);
    clear.mockResolvedValue({ data: null, error: { response: { status: 404 } } });
    const wrapper = await mountBar();

    await statuses(wrapper)[0]!.find("[data-test=\"recipe-request-clear\"]").trigger("click");
    await flushPromises();

    expect(alertError).not.toHaveBeenCalled();
  });
});
