<template>
  <v-chip
    v-if="domain"
    :color="statusColor(status)"
    :to="manageTo"
    size="small"
    variant="tonal"
    :title="tooltip"
    data-testid="recipe-source-status"
  >
    <v-icon start size="small">
      {{ icon }}
    </v-icon>
    {{ $t(statusLabelKey(status)) }}
  </v-chip>
</template>

<script setup lang="ts">
/**
 * What the household thinks of the site a recipe came from, beside its original-URL link.
 *
 * Renders nothing when the URL has no usable host, and reads as neutral ("not on your list")
 * until the household has said something about the site. Clicking through lands on the list so
 * the status can be changed from the recipe that prompted the question.
 */
import { statusColor, statusLabelKey, useRecipeSourceLookup } from "~/composables/use-recipe-sources";

const props = defineProps<{
  url: string | null | undefined;
}>();

const { $globals } = useNuxtApp();
const i18n = useI18n();

const url = computed(() => props.url);
const { found, domain } = useRecipeSourceLookup(url, 0);

const manageTo = "/household/recipe-sources";

// the entry's own status is the typed one; the lookup's top-level `status` is a plain string
const status = computed(() => found.value?.source?.status ?? null);

const icon = computed(() => {
  switch (status.value) {
    case "known-good":
      return $globals.icons.checkboxMarkedCircle;
    case "caution":
      return $globals.icons.alert;
    case "blocked":
      return $globals.icons.close;
    default:
      return $globals.icons.help;
  }
});

const tooltip = computed(() => {
  if (!domain.value) {
    return "";
  }
  const note = found.value?.source?.note;
  const label = i18n.t(statusLabelKey(status.value));
  return note ? `${domain.value}: ${label}. ${note}` : `${domain.value}: ${label}`;
});
</script>
