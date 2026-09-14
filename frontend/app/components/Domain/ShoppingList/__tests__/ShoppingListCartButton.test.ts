import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { createVuetify } from "vuetify";
import { VAlert, VBtn, VCard, VCardText, VIcon } from "vuetify/components";
import { CART_REQUEST_EXTRAS, CART_REQUEST_STATUSES, readCartRequest } from "~/composables/use-cart-request";
import type { ShoppingListOut } from "~/lib/api/types/household";

const { requestCart, clearCartRequest, alertSuccess, alertError } = vi.hoisted(() => ({
  requestCart: vi.fn(),
  clearCartRequest: vi.fn(),
  alertSuccess: vi.fn(),
  alertError: vi.fn(),
}));

vi.mock("~/composables/use-toast", () => ({
  alert: { success: alertSuccess, error: alertError, info: vi.fn() },
}));

vi.mock("~/composables/api", () => ({
  useUserApi: () => ({ shopping: { lists: { requestCart, clearCartRequest } } }),
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

const CartButton = (await import("../ShoppingListCartButton.vue")).default;

// BaseDialog is a global component in the app; here it is a stub that renders its slot and exposes
// the submit action, because what matters is *that* a confirmation stands between the click and
// the request, not how Vuetify draws it.
const BaseDialog = defineComponent({
  name: "BaseDialog",
  props: { modelValue: Boolean, submitText: { type: String, default: "" } },
  emits: ["update:modelValue", "submit"],
  setup: (props, { slots, emit }) => () =>
    props.modelValue
      ? h("div", { "data-test": "confirm-dialog" }, [
          slots.default?.(),
          h(
            "button",
            { "data-test": "confirm-submit", "onClick": () => emit("submit") },
            props.submitText,
          ),
        ])
      : null,
});

function listWith(extras: Record<string, string> = {}, uncheckedItems = 3) {
  return {
    id: "list-1",
    name: "Groceries",
    extras,
    listItems: [
      ...Array.from({ length: uncheckedItems }, (_, i) => ({ id: `u${i}`, checked: false })),
      { id: "c1", checked: true },
    ],
  };
}

function pendingExtras(overrides: Record<string, string> = {}) {
  return {
    [CART_REQUEST_EXTRAS.requestId]: "req-1",
    [CART_REQUEST_EXTRAS.status]: "pending",
    [CART_REQUEST_EXTRAS.requestedAt]: "2026-09-13T18:14:00+00:00",
    [CART_REQUEST_EXTRAS.requestedBy]: "user-1",
    [CART_REQUEST_EXTRAS.requestedByName]: "amanda",
    [CART_REQUEST_EXTRAS.itemCount]: "3",
    ...overrides,
  };
}

// the fixtures carry only the fields the component reads; the cast keeps them readable rather than
// spelling out every field of a shopping list to satisfy the compiler
function mountButton(modelValue: ReturnType<typeof listWith> | null, props: Record<string, unknown> = {}) {
  return mount(CartButton, {
    props: { modelValue: modelValue as unknown as ShoppingListOut | null, ...props },
    global: {
      plugins: [createVuetify({ components: { VAlert, VBtn, VCard, VCardText, VIcon } })],
      components: { BaseDialog },
      mocks: { $globals: { icons } },
    },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  requestCart.mockResolvedValue({ data: { status: "pending" }, error: null });
  clearCartRequest.mockResolvedValue({ data: null, error: null });
});

describe("the confirmation gate", () => {
  test("clicking send does not post anything by itself", async () => {
    const wrapper = mountButton(listWith());

    await wrapper.find("[data-test=\"cart-request-send\"]").trigger("click");
    await flushPromises();

    expect(requestCart).not.toHaveBeenCalled();
    expect(wrapper.find("[data-test=\"confirm-dialog\"]").exists()).toBe(true);
  });

  test("the confirmation names how many items would be bought and that nothing is ordered", async () => {
    const wrapper = mountButton(listWith({}, 7));

    await wrapper.find("[data-test=\"cart-request-send\"]").trigger("click");

    const dialog = wrapper.find("[data-test=\"confirm-dialog\"]").text();
    expect(dialog).toContain("7 items will be added to the Whole Foods cart");
    expect(dialog).toContain("Nothing is ordered");
  });

  test("only confirming posts the request", async () => {
    const wrapper = mountButton(listWith());

    await wrapper.find("[data-test=\"cart-request-send\"]").trigger("click");
    await wrapper.find("[data-test=\"confirm-submit\"]").trigger("click");
    await flushPromises();

    expect(requestCart).toHaveBeenCalledWith("list-1");
    expect(alertSuccess).toHaveBeenCalled();
    expect(wrapper.emitted("changed")).toHaveLength(1);
  });

  test("a list with nothing left to buy cannot be sent", () => {
    const wrapper = mountButton(listWith({}, 0));

    expect(wrapper.find("[data-test=\"cart-request-send\"]").attributes("disabled")).toBeDefined();
  });

  test("an offline list cannot be sent", () => {
    const wrapper = mountButton(listWith(), { disabled: true });

    expect(wrapper.find("[data-test=\"cart-request-send\"]").attributes("disabled")).toBeDefined();
  });

  test("confirming twice while the first is still in flight posts once and claims nothing", async () => {
    let release: (value: unknown) => void = () => {};
    requestCart.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const wrapper = mountButton(listWith());

    await wrapper.find("[data-test=\"cart-request-send\"]").trigger("click");
    await wrapper.find("[data-test=\"confirm-submit\"]").trigger("click");
    await wrapper.find("[data-test=\"confirm-submit\"]").trigger("click");
    release({ data: { status: "pending" }, error: null });
    await flushPromises();

    expect(requestCart).toHaveBeenCalledTimes(1);
    expect(alertSuccess).toHaveBeenCalledTimes(1);
  });

  test("losing the race to the other person says who got there first", async () => {
    requestCart.mockResolvedValue({ data: null, error: { response: { status: 409 } } });
    const wrapper = mountButton(listWith());

    await wrapper.find("[data-test=\"cart-request-send\"]").trigger("click");
    await wrapper.find("[data-test=\"confirm-submit\"]").trigger("click");
    await flushPromises();

    expect(alertError).toHaveBeenCalledWith("Somebody in the household has already asked for this list.");
  });

  test("a refused request says so and does not claim success", async () => {
    requestCart.mockResolvedValue({ data: null, error: { response: { status: 400 } } });
    const wrapper = mountButton(listWith());

    await wrapper.find("[data-test=\"cart-request-send\"]").trigger("click");
    await wrapper.find("[data-test=\"confirm-submit\"]").trigger("click");
    await flushPromises();

    expect(alertError).toHaveBeenCalledWith("Everything on this list is ticked off, so there is nothing to buy.");
    expect(alertSuccess).not.toHaveBeenCalled();
  });
});

describe("what the list shows once a request exists", () => {
  test("a pending request replaces the button, names who asked, and offers to cancel", async () => {
    const wrapper = mountButton(listWith(pendingExtras()));

    expect(wrapper.find("[data-test=\"cart-request-send\"]").exists()).toBe(false);
    const status = wrapper.find("[data-test=\"cart-request-status\"]");
    expect(status.text()).toContain("amanda");
    expect(status.text()).toContain("Waiting to be put in the cart");

    await wrapper.find("[data-test=\"cart-request-clear\"]").trigger("click");
    await flushPromises();
    expect(clearCartRequest).toHaveBeenCalledWith("list-1");
  });

  test("cancelling something already cleared elsewhere is not reported as an error", async () => {
    clearCartRequest.mockResolvedValue({ data: null, error: { response: { status: 404 } } });
    const wrapper = mountButton(listWith(pendingExtras()));

    await wrapper.find("[data-test=\"cart-request-clear\"]").trigger("click");
    await flushPromises();

    expect(alertError).not.toHaveBeenCalled();
    expect(wrapper.emitted("changed")).toHaveLength(1);
  });

  test("a request being filled cannot be cancelled from here", () => {
    const wrapper = mountButton(listWith(pendingExtras({ [CART_REQUEST_EXTRAS.status]: "filling" })));

    expect(wrapper.find("[data-test=\"cart-request-clear\"]").exists()).toBe(false);
    expect(wrapper.find("[data-test=\"cart-request-status\"]").text()).toContain("Filling the cart now");
  });

  test("a finished request shows what the filler reported and can be dismissed", () => {
    const wrapper = mountButton(listWith(pendingExtras({
      [CART_REQUEST_EXTRAS.status]: "filled",
      [CART_REQUEST_EXTRAS.updatedAt]: "2026-09-13T18:31:00+00:00",
      [CART_REQUEST_EXTRAS.result]: "9 in the cart, no organic peaches",
    })));

    const status = wrapper.find("[data-test=\"cart-request-status\"]");
    expect(status.text()).toContain("ready to review and check out");
    expect(status.text()).toContain("9 in the cart, no organic peaches");
    expect(wrapper.find("[data-test=\"cart-request-clear\"]").text()).toBe("Dismiss");
  });

  test("a finished request can be sent again without dismissing it first", async () => {
    const wrapper = mountButton(listWith(pendingExtras({
      [CART_REQUEST_EXTRAS.status]: "filled",
      [CART_REQUEST_EXTRAS.result]: "9 in the cart",
    })));

    const send = wrapper.find("[data-test=\"cart-request-send\"]");
    expect(send.exists()).toBe(true);
    expect(send.text()).toContain("Send again");

    await send.trigger("click");
    await wrapper.find("[data-test=\"confirm-submit\"]").trigger("click");
    await flushPromises();
    expect(requestCart).toHaveBeenCalledWith("list-1");
  });

  test("a request that is still owed offers no way to send another", () => {
    for (const status of ["pending", "filling"]) {
      const wrapper = mountButton(listWith(pendingExtras({ [CART_REQUEST_EXTRAS.status]: status })));
      expect(wrapper.find("[data-test=\"cart-request-send\"]").exists()).toBe(false);
    }
  });

  test("a failed request says so and stays dismissible", () => {
    const wrapper = mountButton(listWith(pendingExtras({
      [CART_REQUEST_EXTRAS.status]: "failed",
      [CART_REQUEST_EXTRAS.result]: "Amazon asked for a sign-in",
    })));

    const status = wrapper.find("[data-test=\"cart-request-status\"]");
    expect(status.text()).toContain("could not be filled");
    expect(status.text()).toContain("Amazon asked for a sign-in");
    expect(wrapper.find("[data-test=\"cart-request-clear\"]").exists()).toBe(true);
  });
});

describe("reading a request off a list", () => {
  test("a list with no request reads as none", () => {
    expect(readCartRequest(listWith() as unknown as ShoppingListOut)).toBeNull();
  });

  test("a half-written or unknown request reads as none rather than throwing", () => {
    expect(readCartRequest(listWith({ [CART_REQUEST_EXTRAS.status]: "pending" }) as unknown as ShoppingListOut)).toBeNull();
    expect(readCartRequest(listWith(pendingExtras({ [CART_REQUEST_EXTRAS.status]: "shopping" })) as unknown as ShoppingListOut)).toBeNull();
  });

  test("an unreadable item count reads as zero, not NaN", () => {
    const request = readCartRequest(
      listWith(pendingExtras({ [CART_REQUEST_EXTRAS.itemCount]: "lots" })) as unknown as ShoppingListOut,
    );

    expect(request?.itemCount).toBe(0);
  });
});

describe("the vocabulary matches the backend copy of record", () => {
  const source = readFileSync(
    resolve(__dirname, "../../../../../../mealie/schema/household/cart_request.py"),
    "utf8",
  );

  test("every extras key the API writes is one this component reads", () => {
    const pythonKeys = [...source.matchAll(/^EXTRA_[A-Z_]+ = "(\w+)"$/gm)].map(match => match[1]).sort();

    expect(pythonKeys).not.toHaveLength(0);
    expect(Object.values(CART_REQUEST_EXTRAS).sort()).toEqual(pythonKeys);
  });

  test("every status the API can write is one this component renders", () => {
    const pythonStatuses = [...source.matchAll(/^CART_REQUEST_(?:PENDING|FILLING|FILLED|FAILED) = "(\w+)"$/gm)]
      .map(match => match[1])
      .sort();

    expect(pythonStatuses).not.toHaveLength(0);
    expect([...CART_REQUEST_STATUSES].sort()).toEqual(pythonStatuses);
  });
});
