import { beforeEach, describe, expect, test, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, h } from "vue";
import { createVuetify } from "vuetify";
import {
  VAlert,
  VCard,
  VCardActions,
  VCardText,
  VContainer,
  VForm,
  VImg,
  VSpacer,
  VSwitch,
  VTextField,
} from "vuetify/components";
import type { CalendarSyncStatus } from "~/lib/api/types/household";

const { get, update, sync, alertSuccess, alertError, user } = vi.hoisted(() => ({
  get: vi.fn(),
  update: vi.fn(),
  sync: vi.fn(),
  alertSuccess: vi.fn(),
  alertError: vi.fn(),
  user: { value: { canManageHousehold: true } as { canManageHousehold: boolean } | null },
}));

vi.mock("~/composables/use-toast", () => ({
  alert: { success: alertSuccess, error: alertError, info: vi.fn() },
}));

vi.mock("~/composables/api", () => ({
  useUserApi: () => ({ calendarSync: { get, update, sync } }),
}));

// Nuxt auto-imports the vitest config does not provide; the page reads them as globals
const icons = new Proxy({}, { get: (_target, name) => String(name) });
vi.stubGlobal("useNuxtApp", () => ({ $globals: { icons } }));
vi.stubGlobal("useSeoMeta", vi.fn());
vi.stubGlobal("useMealieAuth", () => ({ user }));

// Vuetify's inputs measure themselves with a ResizeObserver jsdom does not implement
vi.stubGlobal("ResizeObserver", class {
  observe() {}
  unobserve() {}
  disconnect() {}
});

const GoogleCalendarPage = (await import("../google-calendar.vue")).default;

const ACCOUNT = "mealie-sync@dinner.iam.gserviceaccount.com";
const CALENDAR = "abc123@group.calendar.google.com";

// The app's global components, cut down to what the page asks of them.
const BasePageTitle = defineComponent({
  name: "BasePageTitle",
  setup: (_props, { slots }) => () => h("header", [slots.title?.(), slots.default?.()]),
});

const BaseButton = defineComponent({
  name: "BaseButton",
  inheritAttrs: false,
  props: { disabled: Boolean, loading: Boolean, save: Boolean },
  setup: (props, { slots, attrs }) => () =>
    h("button", { ...attrs, disabled: props.disabled }, slots.default?.() ?? (props.save ? "Save" : "")),
});

const AppButtonCopy = defineComponent({
  name: "AppButtonCopy",
  props: { copyText: { type: String, required: true } },
  setup: props => () => h("button", { "data-testid": "copy-account", "data-copies": props.copyText }),
});

function mirror(overrides: Partial<CalendarSyncStatus> = {}): CalendarSyncStatus {
  return {
    configured: true,
    accountEmail: ACCOUNT,
    calendarId: null,
    enabled: false,
    lastSyncedAt: null,
    lastError: null,
    ...overrides,
  };
}

function serve(status: CalendarSyncStatus) {
  get.mockResolvedValue({ data: status, error: null });
}

async function mountPage() {
  const wrapper = mount(GoogleCalendarPage, {
    global: {
      plugins: [
        createVuetify({
          components: {
            VAlert,
            VCard,
            VCardActions,
            VCardText,
            VContainer,
            VForm,
            VImg,
            VSpacer,
            VSwitch,
            VTextField,
          },
        }),
      ],
      components: { BasePageTitle, BaseButton, AppButtonCopy },
      mocks: { $globals: { icons } },
    },
  });
  await flushPromises();
  return wrapper;
}

type Page = Awaited<ReturnType<typeof mountPage>>;

const part = (wrapper: Page, name: string) => wrapper.find(`[data-testid="${name}"]`);
const idField = (wrapper: Page) => wrapper.find("[data-testid=\"calendar-sync-id\"] input");
const enableSwitch = (wrapper: Page) => wrapper.find("[data-testid=\"calendar-sync-enabled\"] input");

async function submit(wrapper: Page) {
  await wrapper.find("form").trigger("submit");
  await flushPromises();
}

async function pressSyncNow(wrapper: Page) {
  await part(wrapper, "calendar-sync-now").trigger("click");
  await flushPromises();
}

beforeEach(() => {
  vi.clearAllMocks();
  user.value = { canManageHousehold: true };
  serve(mirror());
});

describe("a server with no Google credentials", () => {
  test("says so, names the setting, and offers nothing to fill in", async () => {
    serve(mirror({ configured: false, accountEmail: null }));
    const wrapper = await mountPage();

    const notice = part(wrapper, "calendar-sync-not-configured");
    expect(notice.exists()).toBe(true);
    expect(notice.text()).toContain("not connected to Google yet");
    expect(notice.text()).toContain("GOOGLE_CALENDAR_CREDENTIALS");

    expect(idField(wrapper).exists()).toBe(false);
    expect(part(wrapper, "calendar-sync-save").exists()).toBe(false);
    expect(part(wrapper, "calendar-sync-now").exists()).toBe(false);
    expect(part(wrapper, "calendar-sync-steps").exists()).toBe(false);
  });
});

describe("a server that can reach Google", () => {
  test("explains itself, shows the account to share with, and walks through the three steps", async () => {
    const wrapper = await mountPage();

    expect(wrapper.text()).toContain("Change the plan in Mealie and the calendar follows");
    expect(wrapper.text()).toContain("Move a meal to another day in Google Calendar and Mealie follows");
    expect(part(wrapper, "calendar-sync-not-configured").exists()).toBe(false);

    expect(part(wrapper, "calendar-sync-account").text()).toBe(ACCOUNT);
    expect(part(wrapper, "copy-account").attributes("data-copies")).toBe(ACCOUNT);

    const steps = part(wrapper, "calendar-sync-steps").findAll("li").map(step => step.text());
    expect(steps).toHaveLength(3);
    expect(steps[0]).toContain("create a calendar");
    expect(steps[1]).toContain(ACCOUNT);
    expect(steps[1]).toContain("\"Make changes to events\"");
    expect(steps[2]).toContain("Settings > Integrate calendar");
  });

  test("shows what the household has saved, and when it last synced", async () => {
    serve(mirror({ calendarId: CALENDAR, enabled: true, lastSyncedAt: "2026-11-10T18:30:00+00:00" }));
    const wrapper = await mountPage();

    expect((idField(wrapper).element as HTMLInputElement).value).toBe(CALENDAR);
    expect((enableSwitch(wrapper).element as HTMLInputElement).checked).toBe(true);
    expect(part(wrapper, "calendar-sync-last-synced").text()).toContain("Last synced");
    expect(part(wrapper, "calendar-sync-last-error").exists()).toBe(false);
  });

  test("a mirror that has never run says so", async () => {
    const wrapper = await mountPage();

    expect(part(wrapper, "calendar-sync-last-synced").text()).toBe("Not synced yet");
  });

  test("says the Google account is the whole server's, and that old events stay where they were written", async () => {
    const wrapper = await mountPage();

    const serverWide = part(wrapper, "calendar-sync-server-wide").text();
    expect(serverWide).toContain("one Google account for every household");
    expect(serverWide).toContain("any calendar that account can change");
    expect(serverWide).toContain("a single household");

    const leftBehind = part(wrapper, "calendar-sync-left-behind").text();
    expect(leftBehind).toContain("stay in the old calendar");
    expect(leftBehind).toContain("link a different calendar or turn this off");
  });
});

describe("saving", () => {
  test("sends the trimmed calendar id and the switch, then shows what the server kept", async () => {
    update.mockResolvedValue({ data: mirror({ calendarId: CALENDAR, enabled: true }), error: null });
    const wrapper = await mountPage();
    expect(part(wrapper, "calendar-sync-now").attributes("disabled")).toBeDefined();

    await idField(wrapper).setValue(`  ${CALENDAR}  `);
    await enableSwitch(wrapper).setValue(true);
    await submit(wrapper);

    expect(update).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledWith({ calendarId: CALENDAR, enabled: true });
    expect(alertSuccess).toHaveBeenCalledWith("Calendar settings saved");
    expect((idField(wrapper).element as HTMLInputElement).value).toBe(CALENDAR);
    // the mirror is on now, so it can be synced
    expect(part(wrapper, "calendar-sync-now").attributes("disabled")).toBeUndefined();
    expect(part(wrapper, "calendar-sync-save-error").exists()).toBe(false);
  });

  test("an emptied calendar id is sent as no calendar at all", async () => {
    serve(mirror({ calendarId: CALENDAR }));
    update.mockResolvedValue({ data: mirror(), error: null });
    const wrapper = await mountPage();

    await idField(wrapper).setValue("   ");
    await submit(wrapper);

    expect(update).toHaveBeenCalledWith({ calendarId: null, enabled: false });
  });

  test("a refusal keeps the server's own sentence on the page and claims no success", async () => {
    const refusal = `Mealie could not open that calendar as ${ACCOUNT}. In Google Calendar, share the calendar with `
      + `${ACCOUNT} and choose "Make changes to events", then check the Calendar ID.`;
    update.mockResolvedValue({ data: null, error: { response: { status: 400, data: { detail: { message: refusal } } } } });
    const wrapper = await mountPage();

    await idField(wrapper).setValue(CALENDAR);
    await enableSwitch(wrapper).setValue(true);
    await submit(wrapper);

    expect(part(wrapper, "calendar-sync-save-error").text()).toContain(refusal);
    expect(alertSuccess).not.toHaveBeenCalled();
    // what was typed is still there to be corrected, and the mirror is still off
    expect((idField(wrapper).element as HTMLInputElement).value).toBe(CALENDAR);
    expect(part(wrapper, "calendar-sync-now").attributes("disabled")).toBeDefined();
  });

  test("a failure with nothing to say for itself still says something", async () => {
    update.mockResolvedValue({ data: null, error: new Error("Network Error") });
    const wrapper = await mountPage();

    await submit(wrapper);

    expect(part(wrapper, "calendar-sync-save-error").text()).toContain("The calendar settings could not be saved");
    expect(alertSuccess).not.toHaveBeenCalled();
  });

  test("a member who cannot manage the household sees the settings but cannot change them", async () => {
    user.value = { canManageHousehold: false };
    serve(mirror({ calendarId: CALENDAR, enabled: true }));
    const wrapper = await mountPage();

    expect(idField(wrapper).attributes("disabled")).toBeDefined();
    expect(enableSwitch(wrapper).attributes("disabled")).toBeDefined();
    expect(part(wrapper, "calendar-sync-save").attributes("disabled")).toBeDefined();
    expect(part(wrapper, "calendar-sync-needs-permission").exists()).toBe(true);

    await submit(wrapper);
    expect(update).not.toHaveBeenCalled();

    // syncing is open to every member
    expect(part(wrapper, "calendar-sync-now").attributes("disabled")).toBeUndefined();
  });
});

describe("sync now", () => {
  beforeEach(() => {
    serve(mirror({ calendarId: CALENDAR, enabled: true }));
  });

  test("says what changed and re-reads when the mirror last ran", async () => {
    sync.mockResolvedValue({ data: { created: 2, updated: 1, deleted: 0 }, error: null });
    const wrapper = await mountPage();
    expect(part(wrapper, "calendar-sync-last-synced").text()).toBe("Not synced yet");

    serve(mirror({ calendarId: CALENDAR, enabled: true, lastSyncedAt: "2026-11-10T18:30:00+00:00" }));
    await pressSyncNow(wrapper);

    expect(sync).toHaveBeenCalledTimes(1);
    expect(alertSuccess).toHaveBeenCalledWith("Calendar synced: 2 added, 1 changed, 0 removed");
    expect(alertError).not.toHaveBeenCalled();
    expect(get).toHaveBeenCalledTimes(2);
    expect(part(wrapper, "calendar-sync-last-synced").text()).toContain("Last synced");
  });

  test("a calendar that already matched is reported as that, not as zeroes", async () => {
    sync.mockResolvedValue({ data: { created: 0, updated: 0, deleted: 0 }, error: null });
    const wrapper = await mountPage();

    await pressSyncNow(wrapper);

    expect(alertSuccess).toHaveBeenCalledWith("The calendar already matches the meal plan");
  });

  test("a failure says so and shows why, once the server has recorded it", async () => {
    sync.mockResolvedValue({ data: null, error: new Error("Request failed with status code 502") });
    const wrapper = await mountPage();
    expect(part(wrapper, "calendar-sync-last-error").exists()).toBe(false);

    serve(mirror({ calendarId: CALENDAR, enabled: true, lastError: "Google Calendar answered 503: Backend Error" }));
    await pressSyncNow(wrapper);

    expect(alertSuccess).not.toHaveBeenCalled();
    expect(alertError).toHaveBeenCalledWith("The calendar could not be synced");
    expect(part(wrapper, "calendar-sync-last-error").text()).toContain(
      "The last sync failed: Google Calendar answered 503: Backend Error",
    );
    // the button comes back, so it can be tried again
    expect(part(wrapper, "calendar-sync-now").attributes("disabled")).toBeUndefined();
  });

  test("a failure the server explained is not announced a second time", async () => {
    // the request layer toasts a refusal's own sentence; the page must not stack a generic one on it
    const message = "Google Calendar answered 503: Backend Error";
    sync.mockResolvedValue({ data: null, error: { response: { status: 502, data: { detail: { message } } } } });
    const wrapper = await mountPage();

    await pressSyncNow(wrapper);

    expect(alertError).not.toHaveBeenCalled();
    expect(alertSuccess).not.toHaveBeenCalled();
    expect(get).toHaveBeenCalledTimes(2);
  });

  test("cannot be pressed while the mirror is off", async () => {
    serve(mirror({ calendarId: CALENDAR, enabled: false }));
    const wrapper = await mountPage();

    expect(part(wrapper, "calendar-sync-now").attributes("disabled")).toBeDefined();
  });

  test("re-reading the status does not throw away what somebody was typing", async () => {
    sync.mockResolvedValue({ data: { created: 0, updated: 0, deleted: 0 }, error: null });
    const wrapper = await mountPage();

    await idField(wrapper).setValue("a-different-calendar@group.calendar.google.com");
    await pressSyncNow(wrapper);

    expect((idField(wrapper).element as HTMLInputElement).value).toBe("a-different-calendar@group.calendar.google.com");
  });
});

describe("what the page never does", () => {
  test("it reads the status once on arrival and syncs nothing by itself", async () => {
    await mountPage();

    expect(get).toHaveBeenCalledTimes(1);
    expect(sync).not.toHaveBeenCalled();
    expect(update).not.toHaveBeenCalled();
  });

  test("a status that could not be read leaves the page quiet instead of guessing", async () => {
    get.mockResolvedValue({ data: null, error: new Error("offline") });
    const wrapper = await mountPage();

    expect(part(wrapper, "calendar-sync-not-configured").exists()).toBe(false);
    expect(idField(wrapper).exists()).toBe(false);
    expect(wrapper.text()).toContain("Google Calendar");
  });
});
