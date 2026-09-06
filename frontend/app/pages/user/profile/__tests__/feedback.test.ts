import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, nextTick, ref } from "vue";
import { createVuetify } from "vuetify";
import {
  VBtn,
  VChip,
  VChipGroup,
  VContainer,
  VDataTable,
  VDivider,
  VIcon,
  VImg,
} from "vuetify/components";
import enUS from "~/lang/messages/en-US.json";
import type { UserFeedbackOut } from "~/lib/api/types/user";

const { deleteFeedback, getAll, alertSuccess, alertError } = vi.hoisted(() => ({
  deleteFeedback: vi.fn(),
  getAll: vi.fn(),
  alertSuccess: vi.fn(),
  alertError: vi.fn(),
}));

// real refs, because the page's computeds read them and the tests change them mid-flight
const userFeedback = ref<UserFeedbackOut[]>([]);
const ready = ref(true);

vi.mock("~/composables/use-users", () => ({
  useUserSelfFeedback: () => ({
    userFeedback,
    refreshUserFeedback: vi.fn(),
    setFeedback: vi.fn(),
    deleteFeedback,
    ready,
  }),
}));

vi.mock("~/composables/api", () => ({
  useUserApi: () => ({ recipes: { getAll } }),
}));

vi.mock("~/composables/use-toast", () => ({
  alert: { success: alertSuccess, error: alertError },
}));

// Nuxt auto-imports that the vitest config does not provide; the page reads them as globals
const icons = { thumbDown: "icon-down", thumbUp: "icon-up", minus: "icon-minus", undo: "icon-undo" };
vi.stubGlobal("useMealieAuth", () => ({ user: ref({ id: "user-1", groupSlug: "home" }) }));
vi.stubGlobal("useNuxtApp", () => ({ $globals: { icons } }));
vi.stubGlobal("useSeoMeta", () => {});

// VChipGroup sizes itself with a ResizeObserver jsdom does not implement
vi.stubGlobal("ResizeObserver", class {
  observe() {}
  unobserve() {}
  disconnect() {}
});

const FeedbackPage = (await import("../feedback.vue")).default;

const BasePageTitle = defineComponent({
  setup(_, { slots }) {
    return () => h("div", { class: "page-title" }, [slots.title?.(), slots.default?.()]);
  },
});

const NuxtLink = defineComponent({
  props: { to: { type: String, required: true } },
  setup(props, { slots }) {
    return () => h("a", { href: props.to }, slots.default?.());
  },
});

function event(overrides: Partial<UserFeedbackOut>): UserFeedbackOut {
  return {
    id: "event",
    userId: "user-1",
    recipeId: "recipe",
    vote: "down",
    reason: "too-spicy",
    scope: "recipe",
    target: null,
    note: null,
    advisory: false,
    createdAt: "2026-09-01T00:00:00",
    ...overrides,
  };
}

const mounted: { unmount: () => void }[] = [];

async function mountPage() {
  const wrapper = mount(FeedbackPage, {
    global: {
      plugins: [createVuetify()],
      components: { BasePageTitle, NuxtLink, VBtn, VChip, VChipGroup, VContainer, VDataTable, VDivider, VIcon, VImg },
      mocks: { $globals: { icons } },
    },
  });

  mounted.push(wrapper);
  await flushPromises();
  await nextTick();
  return wrapper;
}

type Page = Awaited<ReturnType<typeof mountPage>>;

function bodyRows(wrapper: Page) {
  return wrapper.findAll("tbody tr");
}

function rowText(wrapper: Page) {
  return bodyRows(wrapper).map(row => row.text().replace(/\s+/g, " ").trim());
}

async function pickFilter(wrapper: Page, label: string) {
  const chip = wrapper.findAll(".v-chip").find(candidate => candidate.text().trim() === label);
  expect(chip, `no filter chip labelled "${label}"`).toBeTruthy();
  await chip!.trigger("click");
  await flushPromises();
  await nextTick();
}

function undoButton(row: ReturnType<Page["find"]>) {
  const button = row.findAll("button").find(candidate => candidate.text().includes(enUS.feedback["undo-vote"]));
  expect(button, "row has no undo button").toBeTruthy();
  return button!;
}

describe("user feedback page", () => {
  beforeEach(() => {
    userFeedback.value = [];
    ready.value = true;
    deleteFeedback.mockReset();
    getAll.mockReset();
    alertSuccess.mockReset();
    alertError.mockReset();

    deleteFeedback.mockResolvedValue({ response: { status: 200 }, data: null, error: null });
    getAll.mockResolvedValue({
      data: {
        items: [
          { id: "noodles", slug: "cold-sesame-noodles", name: "Cold Sesame Noodles" },
          { id: "curry", slug: "green-curry", name: "Green Curry" },
          { id: "toast", slug: "toast", name: "Toast" },
        ],
      },
    });
  });

  afterEach(() => {
    mounted.splice(0).forEach(wrapper => wrapper.unmount());
  });

  test("an empty log says so instead of showing an empty table", async () => {
    const wrapper = await mountPage();

    expect(bodyRows(wrapper)).toHaveLength(1);
    expect(wrapper.text()).toContain(enUS.feedback["no-feedback"]);
    expect(getAll).not.toHaveBeenCalled();
  });

  test("shows only thumbs-down votes by default, one per recipe, newest first", async () => {
    userFeedback.value = [
      event({ id: "noodles-old-up", recipeId: "noodles", vote: "up", reason: null, createdAt: "2026-08-01T00:00:00" }),
      event({ id: "noodles-down", recipeId: "noodles", vote: "down", reason: "not-flavorful-enough", createdAt: "2026-09-05T00:00:00" }),
      event({ id: "curry-down", recipeId: "curry", vote: "down", reason: "too-spicy", createdAt: "2026-09-06T00:00:00" }),
      event({ id: "toast-up", recipeId: "toast", vote: "up", reason: null, createdAt: "2026-09-07T00:00:00" }),
    ];

    const wrapper = await mountPage();
    const rows = rowText(wrapper);

    expect(rows).toHaveLength(2);
    expect(rows[0]).toContain("Green Curry");
    expect(rows[0]).toContain(enUS.feedback.reasons["too-spicy"]);
    expect(rows[1]).toContain("Cold Sesame Noodles");
    expect(rows[1]).toContain(enUS.feedback.reasons["not-flavorful-enough"]);
    expect(wrapper.text()).not.toContain("Toast");

    // the recipe lookup asks for every voted recipe once, by id
    expect(getAll).toHaveBeenCalledTimes(1);
    const filter = getAll.mock.calls[0]![2].queryFilter as string;
    expect(filter.startsWith("id IN [")).toBe(true);
    for (const id of ["noodles", "curry", "toast"]) {
      expect(filter).toContain(`"${id}"`);
    }
  });

  test("recipe names link to the recipe inside the user's group", async () => {
    userFeedback.value = [event({ id: "curry-down", recipeId: "curry" })];

    const wrapper = await mountPage();
    const link = bodyRows(wrapper)[0]!.find("a");

    expect(link.text()).toBe("Green Curry");
    expect(link.attributes("href")).toBe("/g/home/r/green-curry");
  });

  test("a vote on a recipe that no longer exists is still listed, without a link", async () => {
    userFeedback.value = [event({ id: "gone-down", recipeId: "gone" })];

    const wrapper = await mountPage();
    const row = bodyRows(wrapper)[0]!;

    expect(row.find("a").exists()).toBe(false);
    expect(row.text()).toContain(enUS.feedback["recipe-unavailable"]);
    expect(undoButton(row).exists()).toBe(true);
  });

  test("wider scope, target, advisory flag and note are shown under the reason", async () => {
    userFeedback.value = [
      event({
        id: "curry-down",
        recipeId: "curry",
        reason: "too-spicy",
        scope: "cuisine",
        target: "Thai",
        advisory: true,
        note: "kids would not touch it",
      }),
    ];

    const wrapper = await mountPage();
    const row = rowText(wrapper)[0]!;

    expect(row).toContain(`${enUS.feedback.scopes.cuisine}: Thai (${enUS.feedback["advisory-tag"]})`);
    expect(row).toContain("kids would not touch it");
  });

  test("the filter chips switch between down, up and every vote", async () => {
    userFeedback.value = [
      event({ id: "curry-down", recipeId: "curry", vote: "down" }),
      event({ id: "toast-up", recipeId: "toast", vote: "up", reason: null }),
    ];

    const wrapper = await mountPage();
    expect(rowText(wrapper).join(" ")).toContain("Green Curry");
    expect(rowText(wrapper).join(" ")).not.toContain("Toast");

    await pickFilter(wrapper, enUS.feedback["thumbs-up"]);
    expect(rowText(wrapper).join(" ")).toContain("Toast");
    expect(rowText(wrapper).join(" ")).not.toContain("Green Curry");

    await pickFilter(wrapper, enUS.feedback["all-votes"]);
    expect(rowText(wrapper)).toHaveLength(2);

    // a filter with nothing behind it says so, and does not fall back to the empty-log message
    userFeedback.value = [event({ id: "toast-up", recipeId: "toast", vote: "up", reason: null })];
    await pickFilter(wrapper, enUS.feedback["thumbs-down"]);
    expect(wrapper.text()).toContain(enUS.feedback["no-feedback-for-filter"]);
    expect(wrapper.text()).not.toContain(enUS.feedback["no-feedback"]);
  });

  test("undo deletes exactly that vote and reports success", async () => {
    userFeedback.value = [
      event({ id: "curry-down", recipeId: "curry", createdAt: "2026-09-06T00:00:00" }),
      event({ id: "noodles-down", recipeId: "noodles", createdAt: "2026-09-05T00:00:00" }),
    ];

    const wrapper = await mountPage();
    const noodlesRow = bodyRows(wrapper).find(row => row.text().includes("Cold Sesame Noodles"))!;

    await undoButton(noodlesRow).trigger("click");
    await flushPromises();

    expect(deleteFeedback).toHaveBeenCalledTimes(1);
    expect(deleteFeedback).toHaveBeenCalledWith("noodles-down");
    expect(alertSuccess).toHaveBeenCalledWith(enUS.feedback["vote-undone"]);
    expect(alertError).not.toHaveBeenCalled();
  });

  test("a rejected undo is reported as a failure, not a success", async () => {
    deleteFeedback.mockResolvedValue({ response: { status: 404 }, data: null, error: new Error("gone") });
    userFeedback.value = [event({ id: "curry-down", recipeId: "curry" })];

    const wrapper = await mountPage();
    await undoButton(bodyRows(wrapper)[0]!).trigger("click");
    await flushPromises();

    expect(alertError).toHaveBeenCalledWith(enUS.feedback["undo-failed"]);
    expect(alertSuccess).not.toHaveBeenCalled();
  });

  test("the row disappears once the log no longer holds the vote", async () => {
    userFeedback.value = [event({ id: "curry-down", recipeId: "curry" })];
    deleteFeedback.mockImplementation(async (id: string) => {
      userFeedback.value = userFeedback.value.filter(candidate => candidate.id !== id);
      return { response: { status: 200 }, data: null, error: null };
    });

    const wrapper = await mountPage();
    expect(rowText(wrapper)[0]).toContain("Green Curry");

    await undoButton(bodyRows(wrapper)[0]!).trigger("click");
    await flushPromises();
    await nextTick();

    expect(wrapper.text()).not.toContain("Green Curry");
    expect(wrapper.text()).toContain(enUS.feedback["no-feedback"]);
  });
});
