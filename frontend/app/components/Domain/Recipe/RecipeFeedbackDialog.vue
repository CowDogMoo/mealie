<template>
  <BaseDialog
    :model-value="modelValue"
    :title="$t('feedback.title')"
    :icon="$globals.icons.thumbDown"
    :submit-text="$t('feedback.submit')"
    :cancel-text="$t('feedback.cancel')"
    :submit-disabled="!canSubmit"
    :loading="saving"
    can-submit
    keep-open
    @update:model-value="emit('update:modelValue', $event)"
    @submit="submit"
  >
    <v-card-text>
      <v-radio-group
        v-model="reason"
        :label="$t('feedback.reason-label')"
        density="compact"
        hide-details
      >
        <v-radio
          v-for="option in REASONS"
          :key="option"
          :label="$t(`feedback.reasons.${option}`)"
          :value="option"
        />
      </v-radio-group>

      <template v-if="reason">
        <v-select
          v-model="scope"
          :label="$t('feedback.scope-label')"
          :items="scopeOptions"
          :item-title="option => $t(`feedback.scopes.${option.value}`) + (option.advisory ? ` (${$t('feedback.advisory-tag')})` : '')"
          item-value="value"
          density="compact"
          variant="outlined"
          hide-details
          class="mt-4"
        />

        <v-checkbox
          v-model="applyWider"
          :label="$t('feedback.apply-wider')"
          density="compact"
          hide-details
        />

        <v-alert
          v-if="advisory"
          type="info"
          variant="tonal"
          density="compact"
          class="mb-1"
        >
          {{ $t("feedback.advisory") }}
        </v-alert>

        <v-text-field
          v-if="needsTarget"
          v-model="target"
          :label="$t(targetLabelKey)"
          name="feedback-target"
          density="compact"
          variant="outlined"
          autofocus
          hide-details
          class="mt-3"
        />

        <v-textarea
          v-model="note"
          :label="$t('feedback.note')"
          name="feedback-note"
          density="compact"
          variant="outlined"
          rows="2"
          auto-grow
          hide-details
          class="mt-3"
        />
      </template>
    </v-card-text>
  </BaseDialog>
</template>

<script setup lang="ts">
import { useUserSelfFeedback } from "~/composables/use-users";
import type { UserFeedbackIn } from "~/lib/api/types/user";

// Copied from `mealie/schema/user/user_feedback.py` (FEEDBACK_SCOPES / LICENSED_SCOPES), which
// stays the copy of record for the vocabulary. The client needs the map to decide which scopes a
// reason licenses before the request is ever sent; when the two disagree the Python module wins
// and this constant is the one that gets corrected.
const SCOPES = ["recipe", "dish", "ingredient", "cuisine", "source"] as const;

const LICENSED_SCOPES: Record<string, readonly string[]> = {
  "i-did-not-like-this-recipe": ["recipe"],
  "i-do-not-like-this-dish": ["recipe", "dish"],
  "i-do-not-like-a-specific-ingredient": ["recipe", "ingredient"],
  "too-much-work": ["recipe"],
  "took-too-long": ["recipe"],
  "too-heavy": ["recipe"],
  "not-flavorful-enough": ["recipe"],
  "too-spicy": ["recipe"],
  "too-repetitive": ["recipe", "dish", "cuisine"],
  "bad-source": ["recipe", "source"],
  "did-not-work-for-our-household": ["recipe"],
  "other": ["recipe"],
};

const REASONS = Object.keys(LICENSED_SCOPES);

// asking "which ingredient?" beats asking "what exactly?" when we already know the scope
const TARGET_LABELS: Record<string, string> = {
  dish: "feedback.target-dish",
  ingredient: "feedback.target-ingredient",
  cuisine: "feedback.target-cuisine",
  source: "feedback.target-source",
};

interface Props {
  modelValue: boolean;
  recipeId: string;
  slug: string;
}

interface Emits {
  (e: "update:modelValue", value: boolean): void;
  (e: "submitted"): void;
}

const props = defineProps<Props>();
const emit = defineEmits<Emits>();

const { setFeedback } = useUserSelfFeedback();

const reason = ref<string | null>(null);
const scope = ref<string>("recipe");
const applyWider = ref(false);
const target = ref("");
const note = ref("");
const saving = ref(false);

const licensed = computed<readonly string[]>(() => {
  return (reason.value && LICENSED_SCOPES[reason.value]) || ["recipe"];
});

// mirrors is_advisory() in mealie/schema/user/user_feedback.py: a claim that reaches wider than
// its reason licenses is still recorded, it just carries no license to be generalized on its own
const advisory = computed(() => !licensed.value.includes(scope.value));

const scopeOptions = computed(() => {
  const offered = applyWider.value ? SCOPES : licensed.value;
  return offered.map(value => ({ value, advisory: !licensed.value.includes(value) }));
});

const needsTarget = computed(() => scope.value !== "recipe");
const targetLabelKey = computed(() => TARGET_LABELS[scope.value] || "feedback.target");
const canSubmit = computed(() => !!reason.value && (!needsTarget.value || !!target.value.trim()));

// the narrowest scope past the recipe that the reason licenses, which is what each of the four
// wider reasons actually means: name the ingredient, name the dish, name the source
function defaultScope(value: string | null): string {
  const scopes = (value && LICENSED_SCOPES[value]) || ["recipe"];
  return scopes[1] || "recipe";
}

function reset() {
  reason.value = null;
  scope.value = "recipe";
  applyWider.value = false;
  target.value = "";
  note.value = "";
}

watch(reason, (value) => {
  applyWider.value = false;
  scope.value = defaultScope(value);
  target.value = "";
});

watch(applyWider, (wider) => {
  // turning the affordance off must not leave a claim selected that it was holding open
  if (!wider && advisory.value) {
    scope.value = defaultScope(reason.value);
  }
});

watch(scope, (value) => {
  // a target belongs to the scope it was typed for, and recipe scope has no target at all
  if (value === "recipe") {
    target.value = "";
  }
});

watch(() => props.modelValue, (open) => {
  if (open) {
    reset();
  }
});

// the same dialog instance can sit in a card list and be pointed at a different recipe
watch(() => props.recipeId, reset);

async function submit() {
  if (!canSubmit.value || saving.value) {
    return;
  }

  const payload: UserFeedbackIn = {
    vote: "down",
    reason: reason.value,
    scope: scope.value,
    target: needsTarget.value ? target.value.trim() : null,
    note: note.value.trim() || null,
  };

  saving.value = true;
  try {
    await setFeedback(props.slug, payload);
  }
  finally {
    saving.value = false;
  }

  emit("submitted");
  emit("update:modelValue", false);
}
</script>

<style lang="scss" scoped></style>
