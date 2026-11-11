import { beforeEach, describe, expect, test, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h, ref } from "vue";
import { createVuetify } from "vuetify";
import { VAlert, VCard, VCol, VContainer, VProgressLinear, VRow } from "vuetify/components";
import type { MealPlanCalendarView } from "~/lib/api/types/admin";
import type { CalendarSyncStatus } from "~/lib/api/types/household";
import { googleCalendarEmbedUrl, linkedCalendarId } from "~/composables/use-meal-plan-calendar-view";

const { get, appInfo, firstDayOfWeek } = vi.hoisted(() => ({
  get: vi.fn(),
  appInfo: { mealPlanCalendarView: undefined as string | undefined },
  firstDayOfWeek: { value: 1 },
}));

vi.mock("~/composables/api", () => ({
  useUserApi: () => ({ calendarSync: { get } }),
}));

vi.mock("~/composables/use-households", () => ({
  useHouseholdSelf: () => ({
    household: ref({ preferences: { firstDayOfWeek: firstDayOfWeek.value } }),
  }),
}));

// The cards of Mealie's own calendar bring half the app with them; one line each says enough here
vi.mock("~/components/Domain/Recipe/RecipeCardMobile.vue", () => ({
  default: defineComponent({
    name: "RecipeCardMobile",
    props: { name: { type: String, default: "" } },
    setup: props => () => h("div", { "data-testid": "mealplan-entry" }, props.name),
  }),
}));

vi.mock("~/components/Domain/Household/GroupMealPlanDayContextMenu.vue", () => ({
  default: defineComponent({ name: "GroupMealPlanDayContextMenu", setup: () => () => h("div") }),
}));

// A Nuxt auto-import the vitest config does not provide; the composable reads it as a global
vi.stubGlobal("useNuxtApp", () => ({ $appInfo: appInfo }));

const NuxtLink = defineComponent({
  name: "NuxtLink",
  props: { to: { type: String, required: true } },
  setup: (props, { slots, attrs }) => () => h("a", { ...attrs, href: props.to }, slots.default?.()),
});

const PlannerView = (await import("../view.vue")).default;

const CALENDAR = "family123@group.calendar.google.com";

const MEALPLANS = [
  {
    date: new Date(2026, 10, 11),
    meals: [
      {
        id: 1,
        date: "2026-11-11",
        entryType: "dinner",
        title: "Leftover chili",
        text: "",
        recipe: null,
      },
    ],
  },
];

function mirror(overrides: Partial<CalendarSyncStatus> = {}): CalendarSyncStatus {
  return {
    configured: true,
    accountEmail: "mealie-sync@dinner.iam.gserviceaccount.com",
    calendarId: CALENDAR,
    enabled: true,
    lastSyncedAt: null,
    lastError: null,
    ...overrides,
  };
}

function serverShows(view: MealPlanCalendarView | undefined) {
  appInfo.mealPlanCalendarView = view;
}

async function mountView() {
  const wrapper = mount(PlannerView, {
    props: { mealplans: MEALPLANS },
    global: {
      plugins: [createVuetify({ components: { VAlert, VCard, VCol, VContainer, VProgressLinear, VRow } })],
      components: { NuxtLink },
    },
  });
  await flushPromises();
  return wrapper;
}

type View = Awaited<ReturnType<typeof mountView>>;

const embed = (wrapper: View) => wrapper.find("[data-testid=\"mealplan-google-calendar\"]");
const mealieCalendar = (wrapper: View) => wrapper.find("[data-testid=\"mealplan-mealie-calendar\"]");
const unlinkedNote = (wrapper: View) => wrapper.find("[data-testid=\"mealplan-google-calendar-unlinked\"]");

beforeEach(() => {
  vi.clearAllMocks();
  firstDayOfWeek.value = 1;
  serverShows(undefined);
  get.mockResolvedValue({ data: mirror(), error: null });
});

describe("a server set to Mealie's own calendar", () => {
  test.each([["mealie" as const], [undefined]])("shows Mealie's calendar when the setting is %s", async (setting) => {
    serverShows(setting);
    const wrapper = await mountView();

    expect(mealieCalendar(wrapper).exists()).toBe(true);
    expect(wrapper.text()).toContain("Leftover chili");
    expect(embed(wrapper).exists()).toBe(false);
    expect(unlinkedNote(wrapper).exists()).toBe(false);
  });

  test("does not ask about the household's Google calendar, even when one is linked", async () => {
    serverShows("mealie");
    await mountView();

    expect(get).not.toHaveBeenCalled();
  });
});

describe("a server set to the Google calendar", () => {
  beforeEach(() => serverShows("google"));

  test("shows the linked calendar through Google's embed in place of Mealie's calendar", async () => {
    const wrapper = await mountView();

    const frame = embed(wrapper);
    expect(frame.exists()).toBe(true);
    expect(frame.element.tagName).toBe("IFRAME");

    const src = new URL(frame.attributes("src")!);
    expect(src.origin + src.pathname).toBe("https://calendar.google.com/calendar/embed");
    expect(src.searchParams.get("src")).toBe(CALENDAR);
    expect(src.searchParams.get("ctz")).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
    // the household's week starts on Monday: 1 to Mealie, 2 to Google
    expect(src.searchParams.get("wkst")).toBe("2");

    expect(mealieCalendar(wrapper).exists()).toBe(false);
    expect(wrapper.text()).not.toContain("Leftover chili");
    expect(unlinkedNote(wrapper).exists()).toBe(false);
  });

  test("shows neither calendar until it knows whether one is linked", async () => {
    get.mockReturnValue(new Promise(() => {}));
    const wrapper = await mountView();

    expect(embed(wrapper).exists()).toBe(false);
    expect(mealieCalendar(wrapper).exists()).toBe(false);
    expect(unlinkedNote(wrapper).exists()).toBe(false);
  });

  test.each([
    ["no calendar is linked", mirror({ calendarId: null, enabled: false })],
    ["the mirror is switched off", mirror({ enabled: false })],
    ["the server has no Google credentials", mirror({ configured: false, accountEmail: null })],
  ])("falls back to Mealie's calendar with a note when %s", async (_why, status) => {
    get.mockResolvedValue({ data: status, error: null });
    const wrapper = await mountView();

    expect(embed(wrapper).exists()).toBe(false);
    expect(mealieCalendar(wrapper).exists()).toBe(true);
    expect(wrapper.text()).toContain("Leftover chili");

    expect(unlinkedNote(wrapper).exists()).toBe(true);
    const link = wrapper.find("[data-testid=\"mealplan-google-calendar-link\"]");
    expect(link.attributes("href")).toBe("/household/google-calendar");
  });

  test.each([
    ["answers with an error", () => get.mockResolvedValue({ data: null, error: new Error("500") })],
    ["cannot be reached", () => get.mockRejectedValue(new Error("offline"))],
  ])("falls back to Mealie's calendar when the server %s", async (_why, fail) => {
    fail();
    const wrapper = await mountView();

    expect(embed(wrapper).exists()).toBe(false);
    expect(mealieCalendar(wrapper).exists()).toBe(true);
    expect(unlinkedNote(wrapper).exists()).toBe(true);
  });
});

describe("googleCalendarEmbedUrl", () => {
  test("percent-encodes the calendar id", () => {
    const url = googleCalendarEmbedUrl("someone+meals@example.com&mode=AGENDA");

    expect(url).toBe("https://calendar.google.com/calendar/embed?src=someone%2Bmeals%40example.com%26mode%3DAGENDA");
  });

  test("leaves out a time zone and a week start it was not given", () => {
    const params = new URL(googleCalendarEmbedUrl(CALENDAR)).searchParams;

    expect([...params.keys()]).toEqual(["src"]);
  });

  test.each([
    [0, "1"],
    [6, "7"],
  ])("turns Mealie's first day of the week %i into Google's %s", (mealie, google) => {
    const params = new URL(googleCalendarEmbedUrl(CALENDAR, { firstDayOfWeek: mealie })).searchParams;

    expect(params.get("wkst")).toBe(google);
  });

  test("ignores a first day of the week that is not a day", () => {
    const params = new URL(googleCalendarEmbedUrl(CALENDAR, { firstDayOfWeek: 7 })).searchParams;

    expect(params.has("wkst")).toBe(false);
  });
});

describe("linkedCalendarId", () => {
  test("is the calendar of a mirror that is configured and switched on", () => {
    expect(linkedCalendarId(mirror())).toBe(CALENDAR);
  });

  test.each([
    ["nothing is known", null],
    ["the id is blank", mirror({ calendarId: "  " })],
    ["the mirror is off", mirror({ enabled: false })],
    ["the server has no credentials", mirror({ configured: false })],
  ])("is null when %s", (_why, status) => {
    expect(linkedCalendarId(status)).toBeNull();
  });
});
