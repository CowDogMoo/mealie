import { beforeEach, describe, expect, test, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createVuetify } from "vuetify";
import { VBtn, VIcon } from "vuetify/components";
import type { CalendarSyncStatus } from "~/lib/api/types/household";
import { googleCalendarUrl } from "~/lib/google-calendar";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("~/composables/api", () => ({
  useUserApi: () => ({ calendarSync: { get } }),
}));

const GroupMealPlanCalendarLink = (await import("../GroupMealPlanCalendarLink.vue")).default;

const CALENDAR = "family123@group.calendar.google.com";
const icons = new Proxy({}, { get: (_target, name) => String(name) });

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

async function mountLink() {
  const wrapper = mount(GroupMealPlanCalendarLink, {
    global: {
      plugins: [createVuetify({ components: { VBtn, VIcon } })],
      mocks: { $globals: { icons } },
    },
  });
  await flushPromises();
  return wrapper;
}

const link = (wrapper: Awaited<ReturnType<typeof mountLink>>) => wrapper.find("[data-testid=\"mealplan-open-calendar\"]");

beforeEach(() => {
  vi.clearAllMocks();
  get.mockResolvedValue({ data: mirror(), error: null });
});

describe("the planner's way out to Google Calendar", () => {
  test("opens the household's linked calendar in Google Calendar, away from Mealie", async () => {
    const wrapper = await mountLink();

    const button = link(wrapper);
    expect(button.exists()).toBe(true);
    expect(button.text()).toContain("Open Family calendar");
    expect(button.element.tagName).toBe("A");

    const href = new URL(button.attributes("href")!);
    expect(href.origin + href.pathname).toBe("https://calendar.google.com/calendar/r");
    expect(href.searchParams.get("cid")).toBe(CALENDAR);
    expect(button.attributes("target")).toBe("_blank");
    expect(button.attributes("rel")).toContain("noopener");
  });

  test.each([
    ["has linked no calendar", mirror({ calendarId: null, enabled: false })],
    ["has its mirror switched off", mirror({ enabled: false })],
  ])("shows nothing for a household that %s", async (_why, status) => {
    get.mockResolvedValue({ data: status, error: null });
    const wrapper = await mountLink();

    expect(link(wrapper).exists()).toBe(false);
  });

  test.each([
    ["answers with an error", () => get.mockResolvedValue({ data: null, error: new Error("500") })],
    ["cannot be reached", () => get.mockRejectedValue(new Error("offline"))],
  ])("shows nothing, and does not break the planner, when the server %s", async (_why, fail) => {
    fail();
    const wrapper = await mountLink();

    expect(link(wrapper).exists()).toBe(false);
  });
});

describe("googleCalendarUrl", () => {
  test("percent-encodes the calendar id", () => {
    expect(googleCalendarUrl(" someone+meals@example.com&x=1 ")).toBe(
      "https://calendar.google.com/calendar/r?cid=someone%2Bmeals%40example.com%26x%3D1",
    );
  });
});
