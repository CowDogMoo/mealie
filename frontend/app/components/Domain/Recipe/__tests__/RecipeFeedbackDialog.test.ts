import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { computed, defineComponent, h, nextTick } from "vue";
import { createVuetify } from "vuetify";
import {
  VAlert,
  VCardText,
  VCheckbox,
  VRadio,
  VRadioGroup,
  VSelect,
  VTextarea,
  VTextField,
} from "vuetify/components";
import enUS from "~/lang/messages/en-US.json";

const { setFeedback } = vi.hoisted(() => ({ setFeedback: vi.fn() }));

vi.mock("~/composables/use-users", () => ({
  useUserSelfFeedback: () => ({
    userFeedback: computed(() => []),
    refreshUserFeedback: vi.fn(),
    setFeedback,
    deleteFeedback: vi.fn(),
    ready: computed(() => true),
  }),
}));

// The dialog mounts the real Vuetify controls, so the real overlay machinery runs with them, and
// it reaches for two browser APIs jsdom does not implement. Both are only used to size and place
// the menu, which no assertion here depends on; stubbing them is what lets the real VSelect open
// its real list instead of the test asserting against a hand-written select.
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

const RecipeFeedbackDialog = (await import("../RecipeFeedbackDialog.vue")).default;

// BaseDialog is one of ours, not Vuetify's, and it cannot be imported here: it pulls `useNuxtApp`
// from `#app`, an alias only the Nuxt build defines, so the import fails to resolve before
// anything mounts. Teaching vitest that alias is a config change, and the config is owned by no
// leaf of this build. It stands in as a render function carrying the slice of BaseDialog's API
// this dialog uses - the default slot, `submit-disabled`, and a button that emits `submit`.
const BaseDialog = defineComponent({
  props: {
    modelValue: { type: Boolean, default: false },
    title: { type: String, default: "" },
    submitDisabled: { type: Boolean, default: false },
  },
  emits: ["update:modelValue", "submit", "cancel"],
  setup(props, { slots, emit }) {
    return () => h("div", { class: "dialog" }, [
      h("h1", props.title),
      slots.default?.(),
      h("button", {
        class: "dialog-submit",
        disabled: props.submitDisabled,
        onClick: () => emit("submit"),
      }),
    ]);
  },
});

const feedback = enUS.feedback;
const reasons = Object.keys(feedback.reasons) as (keyof typeof feedback.reasons)[];

// Vuetify teleports the scope menu out of the component and into a container on the body, and
// that container outlives the test unless the dialog it belongs to is torn down.
const openDialogs: { unmount: () => void }[] = [];

function mountDialog() {
  const wrapper = mount(RecipeFeedbackDialog, {
    props: { modelValue: true, recipeId: "recipe-uuid", slug: "chicken-thighs" },
    global: {
      plugins: [createVuetify()],
      components: { BaseDialog, VCardText, VRadioGroup, VRadio, VSelect, VCheckbox, VAlert, VTextField, VTextarea },
      mocks: { $globals: { icons: { thumbDown: "M0 0" } } },
    },
  });

  openDialogs.push(wrapper);
  return wrapper;
}

type Dialog = ReturnType<typeof mountDialog>;

// two ticks and a macrotask: enough for the menu's model change to render its content
async function settle() {
  await nextTick();
  await nextTick();
  await new Promise(resolve => setTimeout(resolve, 0));
}

// jsdom performs the checkbox/radio activation behaviour (the control ends up checked) but does
// not dispatch the `input` event a browser fires with it, and `input` is the one Vuetify's
// VSelectionControl listens for. Firing both is what a real click does.
async function check(control: ReturnType<Dialog["find"]>) {
  await control.trigger("click");
  await control.trigger("input");
  await nextTick();
}

function radio(wrapper: Dialog, reason: string) {
  return wrapper.find(`input[type="radio"][value="${reason}"]`);
}

function pickReason(wrapper: Dialog, reason: string) {
  return check(radio(wrapper, reason));
}

function applyWider(wrapper: Dialog) {
  return check(wrapper.find("input[type=\"checkbox\"]"));
}

// VSelect mirrors its items into a hidden native <select> for the browser's own form handling,
// and marks the chosen one selected, so the values it is offering and the value it holds are both
// readable there. The labels are not - those live in the menu, which is opened below.
function scopeChoices(wrapper: Dialog) {
  return wrapper.findAll("option").map(option => option.attributes("value"));
}

function selectedScope(wrapper: Dialog) {
  return (wrapper.find("select").element as HTMLSelectElement).value;
}

function scopeField(wrapper: Dialog) {
  return wrapper.findComponent(VSelect).find("input");
}

// what the field shows the user: VSelect renders the chosen item's title, advisory tag and all
function selectedScopeLabel(wrapper: Dialog) {
  return scopeField(wrapper).element.value;
}

// the menu is teleported to the body, and the field names it: `aria-controls` is the id of the
// menu this select owns, so the items read here can only ever be this dialog's
function menuItems(wrapper: Dialog) {
  const menu = document.getElementById(scopeField(wrapper).attributes("aria-controls") ?? "");
  expect(menu, "the scope menu this select points at is not in the document").toBeTruthy();

  return [...menu!.querySelectorAll(".v-list-item")] as HTMLElement[];
}

async function openScopeMenu(wrapper: Dialog) {
  if (scopeField(wrapper).attributes("aria-expanded") !== "true") {
    await wrapper.findComponent(VSelect).find(".v-field").trigger("mousedown");
    await settle();
  }

  expect(scopeField(wrapper).attributes("aria-expanded")).toBe("true");
}

// the labels the menu is actually offering, read off the open menu
async function scopeLabels(wrapper: Dialog) {
  await openScopeMenu(wrapper);
  return menuItems(wrapper).map(item => item.textContent?.trim());
}

async function chooseScope(wrapper: Dialog, label: string) {
  await openScopeMenu(wrapper);

  const item = menuItems(wrapper).find(candidate => candidate.textContent?.trim() === label);
  expect(item, `the menu offers no "${label}"`).toBeTruthy();

  item!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  await settle();
}

function targetField(wrapper: Dialog) {
  return wrapper.find("input[name=\"feedback-target\"]");
}

function noteField(wrapper: Dialog) {
  return wrapper.find("textarea[name=\"feedback-note\"]");
}

function submitButton(wrapper: Dialog) {
  return wrapper.find("button.dialog-submit");
}

describe("RecipeFeedbackDialog", () => {
  beforeEach(() => {
    setFeedback.mockReset();
    setFeedback.mockResolvedValue(undefined);
  });

  afterEach(() => {
    openDialogs.splice(0).forEach(wrapper => wrapper.unmount());
  });

  test("offers every one of the twelve reasons, by its label", () => {
    const wrapper = mountDialog();

    expect(reasons).toHaveLength(12);
    expect(wrapper.findAllComponents(VRadio)).toHaveLength(12);
    expect(wrapper.findAll("input[type=\"radio\"]")).toHaveLength(12);

    for (const reason of reasons) {
      expect(radio(wrapper, reason).exists()).toBe(true);
      expect(wrapper.text()).toContain(feedback.reasons[reason]);
    }
  });

  test("keeps the scope list to what the reason licenses until asked for more", async () => {
    const wrapper = mountDialog();
    await pickReason(wrapper, "too-much-work");

    expect(scopeChoices(wrapper)).toStrictEqual(["recipe"]);
    expect(await scopeLabels(wrapper)).toStrictEqual([feedback.scopes.recipe]);

    await applyWider(wrapper);

    expect(scopeChoices(wrapper)).toStrictEqual(["recipe", "dish", "ingredient", "cuisine", "source"]);

    // the four the reason cannot license are labelled as such before they are ever chosen
    const labels = await scopeLabels(wrapper);
    expect(labels).toHaveLength(5);
    expect(labels[0]).toBe(feedback.scopes.recipe);
    expect(labels.slice(1).every(label => label?.includes(feedback["advisory-tag"]))).toBe(true);
  });

  test("shows the target field only when the claim reaches past this recipe", async () => {
    const wrapper = mountDialog();

    // the scope list proves the reason took: nothing below this point is asserting on a dialog
    // that quietly never opened its second half
    await pickReason(wrapper, "too-much-work");
    expect(scopeChoices(wrapper)).toStrictEqual(["recipe"]);
    expect(targetField(wrapper).exists()).toBe(false);

    // a reason about an ingredient opens on the ingredient scope: the thing has to be named
    await pickReason(wrapper, "i-do-not-like-a-specific-ingredient");
    expect(scopeChoices(wrapper)).toStrictEqual(["recipe", "ingredient"]);
    expect(targetField(wrapper).exists()).toBe(true);
    expect(wrapper.text()).toContain(feedback["target-ingredient"]);

    await chooseScope(wrapper, feedback.scopes.recipe);
    expect(targetField(wrapper).exists()).toBe(false);
  });

  test("submits the agreed payload", async () => {
    const wrapper = mountDialog();

    await pickReason(wrapper, "i-do-not-like-a-specific-ingredient");
    await targetField(wrapper).setValue("  fennel  ");
    await noteField(wrapper).setValue("  every time  ");
    await submitButton(wrapper).trigger("click");

    expect(setFeedback).toHaveBeenCalledTimes(1);
    expect(setFeedback).toHaveBeenCalledWith("chicken-thighs", {
      vote: "down",
      reason: "i-do-not-like-a-specific-ingredient",
      scope: "ingredient",
      target: "fennel",
      note: "every time",
    });

    expect(wrapper.emitted("submitted")).toHaveLength(1);
    expect(wrapper.emitted("update:modelValue")?.at(-1)).toStrictEqual([false]);
  });

  test("drops a target typed before the claim was narrowed back to this recipe", async () => {
    const wrapper = mountDialog();

    await pickReason(wrapper, "i-do-not-like-a-specific-ingredient");
    await targetField(wrapper).setValue("fennel");
    await chooseScope(wrapper, feedback.scopes.recipe);

    // the name belonged to the claim that was withdrawn, so widening again asks afresh rather
    // than handing back a name the person never retyped
    await chooseScope(wrapper, feedback.scopes.ingredient);
    expect((targetField(wrapper).element as HTMLInputElement).value).toBe("");

    await chooseScope(wrapper, feedback.scopes.recipe);
    await submitButton(wrapper).trigger("click");

    expect(setFeedback).toHaveBeenCalledWith("chicken-thighs", {
      vote: "down",
      reason: "i-do-not-like-a-specific-ingredient",
      scope: "recipe",
      target: null,
      note: null,
    });
  });

  test("surfaces the advisory copy when the claim reaches wider than the reason licenses", async () => {
    const wrapper = mountDialog();

    await pickReason(wrapper, "too-much-work");
    expect(wrapper.text()).not.toContain(feedback.advisory);

    await applyWider(wrapper);
    expect(wrapper.text()).not.toContain(feedback.advisory);

    await chooseScope(wrapper, `${feedback.scopes.cuisine} (${feedback["advisory-tag"]})`);
    expect(selectedScope(wrapper)).toBe("cuisine");
    expect(selectedScopeLabel(wrapper)).toContain(feedback["advisory-tag"]);
    expect(wrapper.text()).toContain(feedback.advisory);

    // an advisory claim is recorded, never refused or quietly narrowed
    await targetField(wrapper).setValue("braises");
    await submitButton(wrapper).trigger("click");

    expect(setFeedback).toHaveBeenCalledWith("chicken-thighs", {
      vote: "down",
      reason: "too-much-work",
      scope: "cuisine",
      target: "braises",
      note: null,
    });
  });

  test("stays quiet when the scope is one the reason licenses", async () => {
    const wrapper = mountDialog();

    await pickReason(wrapper, "too-repetitive");
    expect(selectedScope(wrapper)).toBe("dish");
    expect(selectedScopeLabel(wrapper)).toBe(feedback.scopes.dish);
    expect(wrapper.text()).not.toContain(feedback.advisory);

    await applyWider(wrapper);
    await chooseScope(wrapper, `${feedback.scopes.source} (${feedback["advisory-tag"]})`);
    expect(wrapper.text()).toContain(feedback.advisory);

    // withdrawing the affordance withdraws the claim it was holding open
    await applyWider(wrapper);
    expect(selectedScope(wrapper)).toBe("dish");
    expect(wrapper.text()).not.toContain(feedback.advisory);
  });

  test("refuses to submit until the payload would validate", async () => {
    const wrapper = mountDialog();

    expect(submitButton(wrapper).attributes("disabled")).toBeDefined();
    await submitButton(wrapper).trigger("click");
    expect(setFeedback).not.toHaveBeenCalled();

    // scope reaches past the recipe, so the server would 422 without a target
    await pickReason(wrapper, "i-do-not-like-this-dish");
    expect(submitButton(wrapper).attributes("disabled")).toBeDefined();

    await targetField(wrapper).setValue("   ");
    expect(submitButton(wrapper).attributes("disabled")).toBeDefined();

    await targetField(wrapper).setValue("shepherd's pie");
    expect(submitButton(wrapper).attributes("disabled")).toBeUndefined();

    await submitButton(wrapper).trigger("click");
    expect(setFeedback).toHaveBeenCalledTimes(1);
  });
});
