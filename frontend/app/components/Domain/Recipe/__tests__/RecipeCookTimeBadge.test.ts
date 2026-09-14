import { describe, expect, test, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createVuetify } from "vuetify";
import { VChip, VIcon, VTooltip } from "vuetify/components";
import enUS from "~/lang/messages/en-US.json";
import RecipeCookTimeBadge from "../RecipeCookTimeBadge.vue";

vi.mock("#imports", () => ({}));

const vuetify = createVuetify({ components: { VChip, VIcon, VTooltip } });

function translate(key: string, params?: Record<string, unknown>): string {
  const value = key.split(".").reduce<any>((node, part) => node?.[part], enUS);
  if (typeof value !== "string") {
    throw new Error(`missing translation: ${key}`);
  }
  return value.replace(/\{(\w+)\}/g, (_match, name) => String(params?.[name] ?? ""));
}

function mountBadge(totalMinutes: number | null | undefined, props: Record<string, unknown> = {}) {
  return mount(RecipeCookTimeBadge, {
    props: { totalMinutes, ...props },
    global: {
      plugins: [vuetify],
      stubs: { VTooltip: true },
      mocks: {
        $globals: { icons: { clockOutline: "mdi-clock-outline" } },
      },
    },
  });
}

// `useI18n` and `computed` are auto-imported in the app; supply them here.
vi.stubGlobal("useI18n", () => ({ t: translate }));

describe("the cook-time badge", () => {
  test.each([
    ["nothing at all", 0],
    ["a very short dinner", 10],
    ["a normal weeknight dinner", 30],
    ["the last minute that is still a weeknight", 44],
  ])("shows no badge for %s", async (_label, minutes) => {
    const wrapper = mountBadge(minutes);
    expect(wrapper.find("[data-testid^='cook-time-']").exists()).toBe(false);
    expect(wrapper.text()).toBe("");
  });

  test.each([
    ["the first minute of the band", 45, "45 min"],
    ["the middle of the band", 50, "50 min"],
    ["the last minute of the band", 60, "1 hr"],
  ])("badges %s as needing a yes", async (_label, minutes, expected) => {
    const wrapper = mountBadge(minutes);
    const badge = wrapper.find("[data-testid='cook-time-band']");
    expect(badge.exists()).toBe(true);
    expect(wrapper.find("[data-testid='cook-time-long']").exists()).toBe(false);
    expect(wrapper.text()).toContain(expected);
  });

  test.each([
    ["the first minute past the band", 61, "1 hr 1 min"],
    ["the quiche that started all this", 75, "1 hr 15 min"],
    ["a braise", 125, "2 hr 5 min"],
  ])("badges %s as too long", async (_label, minutes, expected) => {
    const wrapper = mountBadge(minutes);
    const badge = wrapper.find("[data-testid='cook-time-long']");
    expect(badge.exists()).toBe(true);
    expect(wrapper.find("[data-testid='cook-time-band']").exists()).toBe(false);
    expect(wrapper.text()).toContain(expected);
  });

  test.each([
    ["null", null],
    ["undefined", undefined],
  ])("shows no badge when the cook time is %s, rather than guessing", async (_label, minutes) => {
    const wrapper = mountBadge(minutes);
    expect(wrapper.find("[data-testid^='cook-time-']").exists()).toBe(false);
  });

  test("the two levels are told apart by colour, not only by text", async () => {
    const band = mountBadge(50).find("[data-testid='cook-time-band']");
    const long = mountBadge(75).find("[data-testid='cook-time-long']");
    expect(band.classes().join(" ")).toContain("warning");
    expect(long.classes().join(" ")).toContain("error");
  });

  test("carries the reason in an accessible label, not just a colour", async () => {
    const wrapper = mountBadge(75);
    const label = wrapper.find("[data-testid='cook-time-long']").attributes("aria-label");
    expect(label).toContain("1 hr 15 min");
    expect(label).toContain("60 minutes");
  });

  test("sits over the image by default and in the flow when inline", async () => {
    expect(mountBadge(75).find("[data-testid='cook-time-long']").classes()).toContain("recipe-cook-time-badge");
    const inline = mountBadge(75, { inline: true }).find("[data-testid='cook-time-long']");
    expect(inline.classes()).not.toContain("recipe-cook-time-badge");
    expect(inline.classes()).toContain("ml-2");
  });
});
