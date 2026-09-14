<template>
  <v-menu offset-y>
    <template #activator="{ props: activatorProps }">
      <v-btn
        variant="text"
        :icon="$vuetify.display.xs"
        v-bind="activatorProps"
        :color="modelValue === 'any' ? undefined : 'primary'"
        data-testid="cook-time-filter-activator"
      >
        <v-icon :start="!$vuetify.display.xs">
          {{ $globals.icons.clockOutline }}
        </v-icon>
        {{ $vuetify.display.xs ? null : activatorLabel }}
      </v-btn>
    </template>
    <v-list>
      <v-list-item
        v-for="choice in choices"
        :key="choice"
        :active="choice === modelValue"
        :data-testid="`cook-time-choice-${choice}`"
        @click="$emit('update:modelValue', choice)"
      >
        <v-list-item-title>{{ label(choice) }}</v-list-item-title>
      </v-list-item>
    </v-list>
  </v-menu>
</template>

<script setup lang="ts">
/**
 * Narrow the grid to recipes of a given length.
 *
 * The choice is sent to the server, not applied to what happens to be loaded:
 * the grid pages in as you scroll, so a client-side filter would be right on the
 * first screenful and wrong on the second. See `cookTimeQueryFilter`.
 */
import type { CookTimeChoice } from "~/composables/recipes/use-cook-time";

const props = defineProps<{
  modelValue: CookTimeChoice;
}>();

defineEmits<{
  "update:modelValue": [value: CookTimeChoice];
}>();

const i18n = useI18n();

const choices: CookTimeChoice[] = ["any", "weeknight", "band", "long"];

function label(choice: CookTimeChoice): string {
  return i18n.t(`recipe.cook-time-filter-${choice}`);
}

// The button carries the active choice rather than a static "Cook time", so a
// narrowed grid never looks like an empty library.
const activatorLabel = computed(() =>
  props.modelValue === "any" ? i18n.t("recipe.cook-time-filter") : label(props.modelValue),
);
</script>
