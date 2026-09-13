import { beforeEach, describe, expect, test, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, ref } from "vue";
import { createVuetify } from "vuetify";
import { VBtn, VIcon, VTooltip } from "vuetify/components";
import enUS from "~/lang/messages/en-US.json";

interface FeedbackEvent {
  id: string;
  recipeId: string;
  vote: string;
  createdAt: string;
}

const isOwnGroup = ref(true);
const selfFeedback = ref<FeedbackEvent[]>([]);

const { setFeedback, deleteFeedback, alertError } = vi.hoisted(() => ({
  setFeedback: vi.fn(),
  deleteFeedback: vi.fn(),
  alertError: vi.fn(),
}));

vi.mock("~/composables/use-logged-in-state", () => ({
  useLoggedInState: () => ({ isOwnGroup }),
}));

vi.mock("~/composables/use-users", () => ({
  useUserSelfFeedback: () => ({
    userFeedback: selfFeedback,
    refreshUserFeedback: vi.fn(),
    setFeedback,
    deleteFeedback,
    ready: ref(true),
  }),
}));

vi.mock("~/composables/use-toast", () => ({
  alert: { success: vi.fn(), error: alertError },
}));

// The reason dialog is a page of its own; here it only has to say whether it was opened.
vi.mock("../RecipeFeedbackDialog.vue", () => ({
  default: defineComponent({
    name: "RecipeFeedbackDialog",
    props: { modelValue: { type: Boolean, default: false } },
    setup: props => () => h("div", { "data-test": "reason-dialog", "data-open": String(props.modelValue) }),
  }),
}));

// Vuetify's tooltips reach for two browser APIs jsdom does not implement; both only size and
// place an overlay, which nothing here asserts on.
vi.stubGlobal("ResizeObserver", class {
  observe() {}
  unobserve() {}
  disconnect() {}
});

vi.stubGlobal("visualViewport", {
  width: 1024,
  height: 768,
  offsetLeft: 0,
  offsetTop: 0,
  scale: 1,
  addEventListener() {},
  removeEventListener() {},
});

const RecipeFeedbackButtons = (await import("../RecipeFeedbackButtons.vue")).default;

const feedback = enUS.feedback;
const THUMBS_UP = `[aria-label="${feedback["thumbs-up"]}"]`;
const THUMBS_DOWN = `[aria-label="${feedback["thumbs-down"]}"]`;
const FIND_NEW = "[data-test=\"find-new\"]";

function event(vote: string, id = `${vote}-event`, createdAt = "2026-09-01T00:00:00"): FeedbackEvent {
  return { id, recipeId: "recipe-uuid", vote, createdAt };
}

function mountButtons() {
  return mount(RecipeFeedbackButtons, {
    props: { recipeId: "recipe-uuid", slug: "chicken-thighs-with-fennel" },
    global: {
      plugins: [createVuetify()],
      components: { VBtn, VIcon, VTooltip },
      mocks: {
        $globals: {
          icons: {
            thumbUp: "M1 1",
            thumbUpOutline: "M2 2",
            thumbDown: "M3 3",
            thumbDownOutline: "M4 4",
            diceMultiple: "M5 5",
          },
        },
      },
    },
  });
}

describe("taking a vote back from the thumbs", () => {
  beforeEach(() => {
    isOwnGroup.value = true;
    selfFeedback.value = [];
    setFeedback.mockReset();
    setFeedback.mockResolvedValue(undefined);
    deleteFeedback.mockReset();
    deleteFeedback.mockResolvedValue({ response: { status: 200 } });
    alertError.mockReset();
  });

  test("pressing the lit thumbs up undoes the vote instead of casting it again", async () => {
    selfFeedback.value = [event("up")];
    const wrapper = mountButtons();

    expect(wrapper.find(THUMBS_UP).attributes("aria-pressed")).toBe("true");

    await wrapper.find(THUMBS_UP).trigger("click");
    await flushPromises();

    expect(deleteFeedback).toHaveBeenCalledTimes(1);
    expect(deleteFeedback).toHaveBeenCalledWith("up-event");
    expect(setFeedback).not.toHaveBeenCalled();
    expect(alertError).not.toHaveBeenCalled();
  });

  test("pressing an unlit thumbs up still casts the vote", async () => {
    const wrapper = mountButtons();

    expect(wrapper.find(THUMBS_UP).attributes("aria-pressed")).toBe("false");

    await wrapper.find(THUMBS_UP).trigger("click");
    await flushPromises();

    expect(setFeedback).toHaveBeenCalledWith("chicken-thighs-with-fennel", { vote: "up" });
    expect(deleteFeedback).not.toHaveBeenCalled();
  });

  test("pressing the lit thumbs down undoes it without asking why all over again", async () => {
    selfFeedback.value = [event("down")];
    const wrapper = mountButtons();

    expect(wrapper.find(THUMBS_DOWN).attributes("aria-pressed")).toBe("true");

    await wrapper.find(THUMBS_DOWN).trigger("click");
    await flushPromises();

    expect(deleteFeedback).toHaveBeenCalledWith("down-event");
    expect(wrapper.find("[data-test=\"reason-dialog\"]").attributes("data-open")).toBe("false");
  });

  test("pressing an unlit thumbs down still asks why", async () => {
    const wrapper = mountButtons();

    await wrapper.find(THUMBS_DOWN).trigger("click");
    await flushPromises();

    expect(wrapper.find("[data-test=\"reason-dialog\"]").attributes("data-open")).toBe("true");
    expect(deleteFeedback).not.toHaveBeenCalled();
  });

  test("undoes only the event the thumb is showing, so an older vote becomes the answer again", async () => {
    selfFeedback.value = [
      event("down", "older-down", "2026-09-01T00:00:00"),
      event("up", "newer-up", "2026-09-02T00:00:00"),
    ];
    const wrapper = mountButtons();

    await wrapper.find(THUMBS_UP).trigger("click");
    await flushPromises();

    expect(deleteFeedback).toHaveBeenCalledTimes(1);
    expect(deleteFeedback).toHaveBeenCalledWith("newer-up");
  });

  test("says so when the undo does not go through", async () => {
    selfFeedback.value = [event("up")];
    deleteFeedback.mockResolvedValue({ response: { status: 404 } });
    const wrapper = mountButtons();

    await wrapper.find(THUMBS_UP).trigger("click");
    await flushPromises();

    expect(alertError).toHaveBeenCalledWith(feedback["undo-failed"]);
  });

  test("the die is not a toggle: pressing it again is a fresh request", async () => {
    selfFeedback.value = [event("refill", "refill-event", new Date().toISOString())];
    const wrapper = mountButtons();

    await wrapper.find(FIND_NEW).trigger("click");
    await flushPromises();

    expect(setFeedback).toHaveBeenCalledWith("chicken-thighs-with-fennel", { vote: "refill" });
    expect(deleteFeedback).not.toHaveBeenCalled();
  });
});
