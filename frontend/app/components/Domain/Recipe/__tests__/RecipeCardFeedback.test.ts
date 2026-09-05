import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent, h, ref } from "vue";
import { createVuetify } from "vuetify";
import {
  VBtn,
  VCard,
  VCardActions,
  VCardText,
  VCardTitle,
  VExpandTransition,
  VHover,
  VIcon,
  VSpacer,
  VTooltip,
} from "vuetify/components";
import enUS from "~/lang/messages/en-US.json";

// real refs, because the card's template leans on ref unwrapping to gate the badge and the menu
const isOwnGroup = ref(true);
const selfFeedback = ref<{ recipeId: string; vote: string; createdAt: string }[]>([]);

// the mock factories are hoisted, but they only run when the card is imported below, by which
// point both refs above exist
vi.mock("~/composables/use-logged-in-state", () => ({
  useLoggedInState: () => ({ isOwnGroup }),
}));

vi.mock("~/composables/use-users", () => ({
  useUserSelfFeedback: () => ({
    userFeedback: selfFeedback,
    refreshUserFeedback: vi.fn(),
    setFeedback: vi.fn(),
    deleteFeedback: vi.fn(),
    ready: ref(true),
  }),
}));

// The card's own children each reach for the API layer, and none of them is what this test is
// about: the question is only whether the card puts the feedback buttons in the action row. They
// are stood in for by markers so their presence (and their order in the row) can still be asserted.
function marker(name: string) {
  return defineComponent({
    name,
    inheritAttrs: false,
    setup: (_props, { attrs }) => () => h("div", { ...attrs, "data-test": name }),
  });
}

vi.mock("../RecipeCardImage.vue", () => ({ default: marker("recipe-image") }));
vi.mock("../RecipeFavoriteBadge.vue", () => ({ default: marker("favorite-badge") }));
vi.mock("../RecipeCardRating.vue", () => ({ default: marker("card-rating") }));
vi.mock("../RecipeChips.vue", () => ({ default: marker("recipe-chips") }));
vi.mock("../RecipeContextMenu/RecipeContextMenu.vue", () => ({ default: marker("context-menu") }));

// The real Vuetify components below carry the real overlay machinery - the thumbs each own a
// tooltip - and it reaches for two browser APIs jsdom does not implement. Both only size and
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

// Two of the card's components are ours rather than Vuetify's, and neither can be imported here:
// BaseDialog pulls `useNuxtApp` from `#app` and SafeMarkdown calls `useNuxtApp()`, both of which
// only exist inside the Nuxt build. Teaching vitest that alias is a config change, and the config
// is owned by no leaf of this build, so they stand in as render functions. BaseDialog keeps the
// modelValue gate the real one has, so the closed feedback dialog contributes nothing to the card.
const BaseDialog = defineComponent({
  name: "BaseDialog",
  props: { modelValue: { type: Boolean, default: false } },
  setup: (props, { slots }) => () => (props.modelValue ? h("div", { class: "dialog" }, slots.default?.()) : null),
});

const SafeMarkdown = defineComponent({
  name: "SafeMarkdown",
  props: { source: { type: String, default: "" } },
  setup: props => () => h("div", props.source),
});

vi.stubGlobal("useMealieAuth", () => ({ user: { value: { id: "user-uuid", groupSlug: "home" } } }));
vi.stubGlobal("useRoute", () => ({ params: {} }));

const RecipeCard = (await import("../RecipeCard.vue")).default;
const RecipeFeedbackButtons = (await import("../RecipeFeedbackButtons.vue")).default;

const feedback = enUS.feedback;
const THUMBS_UP = `[aria-label="${feedback["thumbs-up"]}"]`;
const THUMBS_DOWN = `[aria-label="${feedback["thumbs-down"]}"]`;

// each thumb owns a tooltip, and Vuetify teleports tooltip content to a container on the body
// that outlives the test unless the card it belongs to is torn down
const openCards: { unmount: () => void }[] = [];

function mountCard(props: Record<string, unknown> = {}) {
  const wrapper = mount(RecipeCard, {
    props: {
      name: "Chicken thighs with fennel",
      slug: "chicken-thighs-with-fennel",
      description: "A weeknight tray bake.",
      recipeId: "recipe-uuid",
      ...props,
    },
    global: {
      plugins: [createVuetify()],
      components: {
        VBtn,
        VCard,
        VCardActions,
        VCardText,
        VCardTitle,
        VExpandTransition,
        VHover,
        VIcon,
        VSpacer,
        VTooltip,
        BaseDialog,
        SafeMarkdown,
      },
      mocks: {
        $globals: {
          icons: {
            dotsVertical: "M0 0",
            thumbUp: "M1 1",
            thumbUpOutline: "M2 2",
            thumbDown: "M3 3",
            thumbDownOutline: "M4 4",
          },
        },
      },
    },
  });

  openCards.push(wrapper);
  return wrapper;
}

// every assertion below is made against a card that demonstrably rendered: if the mount had
// failed, this would be missing too, and "no feedback buttons" would pass for the wrong reason
function expectCardRendered(wrapper: ReturnType<typeof mountCard>) {
  expect(wrapper.text()).toContain("Chicken thighs with fennel");
  expect(wrapper.find("[data-test=\"favorite-badge\"]").exists()).toBe(true);
  expect(wrapper.find("[data-test=\"card-rating\"]").exists()).toBe(true);
  expect(wrapper.find("[data-test=\"context-menu\"]").exists()).toBe(true);
}

describe("RecipeCard feedback controls", () => {
  beforeEach(() => {
    isOwnGroup.value = true;
    selfFeedback.value = [];
  });

  afterEach(() => {
    openCards.splice(0).forEach(wrapper => wrapper.unmount());
  });

  test("puts both thumbs in the action row when the caller opts in", () => {
    const wrapper = mountCard({ showFeedback: true });

    expectCardRendered(wrapper);
    expect(wrapper.findComponent(RecipeFeedbackButtons).exists()).toBe(true);
    expect(wrapper.find(THUMBS_UP).exists()).toBe(true);
    expect(wrapper.find(THUMBS_DOWN).exists()).toBe(true);
    expect(wrapper.find(THUMBS_DOWN).attributes("disabled")).toBeUndefined();
  });

  test("renders today's card when the prop is omitted", () => {
    const wrapper = mountCard();

    expectCardRendered(wrapper);
    expect(wrapper.findComponent(RecipeFeedbackButtons).exists()).toBe(false);
    expect(wrapper.find(THUMBS_UP).exists()).toBe(false);
    expect(wrapper.find(THUMBS_DOWN).exists()).toBe(false);
  });

  test("renders today's card when the prop is explicitly false", () => {
    const wrapper = mountCard({ showFeedback: false });

    expectCardRendered(wrapper);
    expect(wrapper.findComponent(RecipeFeedbackButtons).exists()).toBe(false);
    expect(wrapper.find(THUMBS_DOWN).exists()).toBe(false);
  });

  test("offers no vote to a viewer from another group, just as it offers no favourite badge", () => {
    isOwnGroup.value = false;
    const wrapper = mountCard({ showFeedback: true });

    expect(wrapper.text()).toContain("Chicken thighs with fennel");
    expect(wrapper.find("[data-test=\"favorite-badge\"]").exists()).toBe(false);
    expect(wrapper.find("[data-test=\"context-menu\"]").exists()).toBe(false);
    expect(wrapper.findComponent(RecipeFeedbackButtons).exists()).toBe(false);
  });

  test("offers no vote on a card with no recipe behind it", () => {
    const wrapper = mountCard({ showFeedback: true, recipeId: "", slug: "" });

    expect(wrapper.text()).toContain("Chicken thighs with fennel");
    expect(wrapper.findComponent(RecipeFeedbackButtons).exists()).toBe(false);
  });

  test("keeps one row: favourite, rating, thumbs, then the menu", () => {
    const wrapper = mountCard({ showFeedback: true });
    const html = wrapper.html();

    expect(html.indexOf("favorite-badge")).toBeGreaterThan(-1);
    expect(html.indexOf("favorite-badge")).toBeLessThan(html.indexOf("card-rating"));
    expect(html.indexOf("card-rating")).toBeLessThan(html.indexOf(feedback["thumbs-up"]));
    expect(html.indexOf(feedback["thumbs-up"])).toBeLessThan(html.indexOf("context-menu"));

    // this card only renders on md-and-up grids, so the thumbs always take the compact size
    expect(wrapper.findComponent(RecipeFeedbackButtons).props("small")).toBe(true);
    expect(wrapper.find(THUMBS_UP).classes()).toContain("v-btn--size-x-small");
  });
});
