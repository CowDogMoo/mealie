<template>
  <v-chip
    v-if="level === 'band' || level === 'long'"
    :color="level === 'long' ? 'error' : 'warning'"
    size="small"
    variant="flat"
    label
    :class="inline ? 'ml-2 flex-shrink-0' : 'recipe-cook-time-badge'"
    :data-testid="`cook-time-${level}`"
    :aria-label="tooltip"
  >
    <v-icon start size="small">
      {{ $globals.icons.clockOutline }}
    </v-icon>
    {{ time }}
    <v-tooltip
      activator="parent"
      location="bottom"
    >
      {{ tooltip }}
    </v-tooltip>
  </v-chip>
</template>

<script setup lang="ts">
/**
 * How long this is, shown only when the length is worth knowing before you open it.
 *
 * A weeknight-length recipe gets no badge. Putting "25 min" on almost every card
 * would make the grid noisier without telling anybody anything — the badge earns
 * its place precisely because it is rare, so a dinner that will not fit a Tuesday
 * stands out while you are still choosing.
 *
 * Reads `totalMinutes`, which the server parsed from the recipe's own cook time.
 * A recipe whose time the server could not read shows nothing rather than
 * guessing, which matches the filter: it belongs to no length.
 */
import { cookTimeLevel, formatCookTime } from "~/composables/recipes/use-cook-time";

const props = defineProps<{
  totalMinutes?: number | null;
  /**
   * Sit in the flow instead of over the card image. The mobile card is a list
   * row whose thumbnail is too small to carry a chip without covering the food.
   */
  inline?: boolean;
}>();

const i18n = useI18n();

const level = computed(() => cookTimeLevel(props.totalMinutes));
const time = computed(() => formatCookTime(props.totalMinutes));

const tooltip = computed(() => {
  const key = level.value === "long" ? "recipe.cook-time-long-tooltip" : "recipe.cook-time-band-tooltip";
  return i18n.t(key, { time: time.value });
});
</script>

<style scoped>
.recipe-cook-time-badge {
  /* Sits over the card image, out of the way of the favourite badge on the left. */
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 2;
  opacity: 0.94;
}
</style>
