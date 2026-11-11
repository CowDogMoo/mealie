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

const { setFeedback, deleteFeedback, alertSuccess, alertError } = vi.hoisted(() => ({
  setFeedback: vi.fn(),
  deleteFeedback: vi.fn(),
  alertSuccess: vi.fn(),
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
  alert: { success: alertSuccess, error: alertError },
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
const DISMISS = "[data-test=\"dismiss\"]";

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
    setFeedback.mockResolvedValue({ response: { status: 201 } });
    deleteFeedback.mockReset();
    deleteFeedback.mockResolvedValue({ response: { status: 200 } });
    alertSuccess.mockReset();
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

describe("the x on a card that stays on screen", () => {
  beforeEach(() => {
    isOwnGroup.value = true;
    selfFeedback.value = [];
    setFeedback.mockReset();
    setFeedback.mockResolvedValue({ response: { status: 201 } });
    deleteFeedback.mockReset();
    alertSuccess.mockReset();
    alertError.mockReset();
  });

  test("every press is sent: whether the recipe is already removed is the server's to say", async () => {
    const wrapper = mountButtons();

    await wrapper.find(DISMISS).trigger("click");
    await flushPromises();

    // this tab's copy of the log now shows the removal, and the card is still on screen
    selfFeedback.value = [event("dismiss", "dismiss-event", new Date().toISOString())];

    await wrapper.find(DISMISS).trigger("click");
    await flushPromises();

    expect(setFeedback).toHaveBeenCalledTimes(2);
    expect(setFeedback).toHaveBeenLastCalledWith("chicken-thighs-with-fennel", { vote: "dismiss" });
    expect(alertSuccess).toHaveBeenCalledTimes(2);
    expect(alertSuccess).toHaveBeenLastCalledWith(feedback["dismissed"]);
    expect(alertError).not.toHaveBeenCalled();
  });

  test("a log that still shows a removal undone elsewhere does not stand in for the request", async () => {
    selfFeedback.value = [event("dismiss", "undone-on-another-device", "2026-09-01T00:00:00")];
    setFeedback.mockResolvedValue({ response: null, error: new Error("Network Error"), data: null });
    const wrapper = mountButtons();

    await wrapper.find(DISMISS).trigger("click");
    await flushPromises();

    expect(setFeedback).toHaveBeenCalledTimes(1);
    expect(setFeedback).toHaveBeenCalledWith("chicken-thighs-with-fennel", { vote: "dismiss" });
    expect(alertSuccess).not.toHaveBeenCalled();
    expect(alertError).toHaveBeenCalledWith(feedback["save-failed"]);
  });

  test.each([
    ["the x", DISMISS, "dismiss"],
    ["the die", FIND_NEW, "refill"],
  ])("%s says so, and confirms nothing, when the request does not go through", async (_name, button, vote) => {
    setFeedback.mockResolvedValue({ response: null, error: new Error("Network Error"), data: null });
    const wrapper = mountButtons();

    await wrapper.find(button).trigger("click");
    await flushPromises();

    expect(setFeedback).toHaveBeenCalledWith("chicken-thighs-with-fennel", { vote });
    expect(alertSuccess).not.toHaveBeenCalled();
    expect(alertError).toHaveBeenCalledTimes(1);
    expect(alertError).toHaveBeenCalledWith(feedback["save-failed"]);
  });

  test("a rejected request is not confirmed either", async () => {
    setFeedback.mockResolvedValue({ response: { status: 403 }, error: null, data: null });
    const wrapper = mountButtons();

    await wrapper.find(DISMISS).trigger("click");
    await flushPromises();

    expect(alertSuccess).not.toHaveBeenCalled();
    expect(alertError).toHaveBeenCalledWith(feedback["save-failed"]);
  });
});
