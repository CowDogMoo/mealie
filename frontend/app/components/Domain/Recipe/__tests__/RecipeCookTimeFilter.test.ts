import { beforeEach, describe, expect, test, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";
import * as directives from "vuetify/directives";
import { combineQueryFilters, cookTimeQueryFilter } from "~/composables/recipes/use-cook-time";
import RecipeCookTimeFilter from "../RecipeCookTimeFilter.vue";

/**
 * The cook-time filter has to reach the server, not the loaded page.
 *
 * The grid pages in as you scroll, so a filter applied to what is already in
 * memory looks right on the first screenful and lies on the second. These tests
 * assert the query string that leaves the browser, and the exact strings they
 * assert are the ones proven to filter correctly in
 * tests/integration_tests/user_recipe_tests/test_recipe_total_minutes_filter.py.
 */

const fetchMore = vi.fn(async () => []);

vi.mock("~/composables/recipes", () => ({
  useLazyRecipes: () => ({ fetchMore, getRandom: vi.fn(async () => null) }),
}));
vi.mock("~/composables/use-logged-in-state", () => ({
  useLoggedInState: () => ({ isOwnGroup: ref(true), isLoggedIn: ref(true) }),
}));
vi.mock("~/composables/use-users", () => ({
  useUserSelfFeedback: () => ({ selfFeedback: ref([]), refreshFeedback: vi.fn() }),
}));
vi.mock("~/composables/use-users/feedback-log", () => ({
  hiddenRecipeIds: () => [],
}));
vi.mock("~/composables/use-users/preferences", () => ({
  useUserSortPreferences: () => ref({
    orderBy: "name",
    orderDirection: "asc",
    filterNull: false,
    sortIcon: "mdi-sort",
  }),
}));
vi.stubGlobal("useMealieAuth", () => ({ user: ref({ groupSlug: "home" }) }));
vi.stubGlobal("useRoute", () => ({ params: { groupSlug: "home" }, path: "/g/home/r" }));
vi.stubGlobal("useRouter", () => ({ push: vi.fn() }));
vi.stubGlobal("useNuxtApp", () => ({ $globals: { icons: {} } }));
// Nuxt auto-imports this one, so vi.mock on its module has nothing to intercept.
vi.stubGlobal("useScrollPosition", () => ({
  savePosition: () => {},
  getSavedPage: () => null,
  restorePosition: () => {},
}));
// jsdom ships neither of these, and Vuetify's overlay and layout code want both.
vi.stubGlobal("ResizeObserver", class {
  observe() {}
  unobserve() {}
  disconnect() {}
});
// The grid asks Vuetify which layout it is in; jsdom has no viewport to answer with.
vi.stubGlobal("useDisplay", () => ({
  xs: ref(false),
  smAndDown: ref(false),
  mdAndUp: ref(true),
  name: ref("lg"),
}));
// Vuetify's overlay positioning reads window.visualViewport, which jsdom omits.
vi.stubGlobal("visualViewport", {
  addEventListener: () => {},
  removeEventListener: () => {},
  width: 1280,
  height: 800,
  offsetLeft: 0,
  offsetTop: 0,
  scale: 1,
});

const vuetify = createVuetify({ components, directives });

/** The 7th argument of fetchMore is the queryFilter the server is asked for. */
function lastQueryFilter(): string | null {
  const call = fetchMore.mock.calls.at(-1) as unknown[] | undefined;
  return (call?.[6] as string | null) ?? null;
}

async function mountSection(props: Record<string, unknown> = {}) {
  const RecipeCardSection = (await import("../RecipeCardSection.vue")).default;
  const wrapper = mount(RecipeCardSection, {
    props,
    global: {
      plugins: [vuetify],
      stubs: {
        RecipeCard: true,
        RecipeCardMobile: true,
        VMenu: false,
      },
      mocks: {
        $globals: { icons: {} },
        $vuetify: { display: { xs: false } },
      },
    },
  });
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  fetchMore.mockClear();
});

describe("the cook-time filter control", () => {
  test("starts on 'any' and offers every length", async () => {
    const wrapper = mount(RecipeCookTimeFilter, {
      props: { modelValue: "any" },
      global: { plugins: [vuetify], mocks: { $globals: { icons: {} }, $vuetify: { display: { xs: false } } } },
    });
    expect(wrapper.text()).toContain("Cook time");
  });

  test("emits the chosen length", async () => {
    const wrapper = mount(RecipeCookTimeFilter, {
      props: { modelValue: "any" },
      global: {
        plugins: [vuetify],
        mocks: { $globals: { icons: {} }, $vuetify: { display: { xs: false } } },
      },
      attachTo: document.body,
    });

    await wrapper.find("[data-testid='cook-time-filter-activator']").trigger("click");
    await flushPromises();

    const choice = document.querySelector("[data-testid='cook-time-choice-long']") as HTMLElement | null;
    expect(choice, "the menu should list the too-long choice").not.toBeNull();
    choice!.click();
    await flushPromises();

    expect(wrapper.emitted("update:modelValue")?.at(-1)).toEqual(["long"]);
    wrapper.unmount();
  });

  test("names the active choice on the button, so a narrowed grid is not mistaken for an empty one", async () => {
    const wrapper = mount(RecipeCookTimeFilter, {
      props: { modelValue: "long" },
      global: { plugins: [vuetify], mocks: { $globals: { icons: {} }, $vuetify: { display: { xs: false } } } },
    });
    expect(wrapper.text()).toContain("Too long");
    expect(wrapper.text()).not.toContain("Cook time");
  });
});

describe("the query the grid sends", () => {
  test("asks for everything until a length is chosen", async () => {
    await mountSection();
    expect(fetchMore).toHaveBeenCalled();
    expect(lastQueryFilter()).toBeNull();
  });

  test("choosing a length narrows the server query, and clearing it restores the unfiltered one", async () => {
    const wrapper = await mountSection();
    const filter = wrapper.findComponent(RecipeCookTimeFilter);
    expect(filter.exists()).toBe(true);

    await filter.vm.$emit("update:modelValue", "long");
    await flushPromises();
    expect(lastQueryFilter()).toBe("totalMinutes > 60");

    await filter.vm.$emit("update:modelValue", "weeknight");
    await flushPromises();
    expect(lastQueryFilter()).toBe("totalMinutes < 45");

    await filter.vm.$emit("update:modelValue", "any");
    await flushPromises();
    expect(lastQueryFilter()).toBeNull();
  });

  test("keeps the page's own filter rather than replacing it", async () => {
    const wrapper = await mountSection({ query: { queryFilter: "tags.name IN [\"dinner\"]" } });
    const filter = wrapper.findComponent(RecipeCookTimeFilter);

    await filter.vm.$emit("update:modelValue", "band");
    await flushPromises();

    const sent = lastQueryFilter();
    expect(sent).toContain("tags.name");
    expect(sent).toContain("totalMinutes >= 45 AND totalMinutes <= 60");
    expect(sent).toBe("(tags.name IN [\"dinner\"]) AND (totalMinutes >= 45 AND totalMinutes <= 60)");
  });

  test("can be turned off where a cook-time filter makes no sense", async () => {
    const wrapper = await mountSection({ disableCookTimeFilter: true });
    expect(wrapper.findComponent(RecipeCookTimeFilter).exists()).toBe(false);
  });
});

describe("the query strings themselves", () => {
  // These exact strings are the contract with the backend; the integration test
  // proves each one returns the right recipes.
  test.each([
    ["any", null],
    ["weeknight", "totalMinutes < 45"],
    ["band", "totalMinutes >= 45 AND totalMinutes <= 60"],
    ["long", "totalMinutes > 60"],
  ] as const)("%s maps to %s", (choice, expected) => {
    expect(cookTimeQueryFilter(choice)).toBe(expected);
  });

  test("combining brackets each side, so an OR on either cannot swallow the other", () => {
    expect(combineQueryFilters(null, null)).toBeNull();
    expect(combineQueryFilters("a = 1", null)).toBe("a = 1");
    expect(combineQueryFilters(null, "b = 2")).toBe("b = 2");
    expect(combineQueryFilters("a = 1 OR a = 2", "b = 2")).toBe("(a = 1 OR a = 2) AND (b = 2)");
    expect(combineQueryFilters("  ", "b = 2")).toBe("b = 2");
  });
});
