<template>
  <div>
    <iframe
      :src="src"
      :title="$t('calendar-sync.view-embed-title')"
      class="google-calendar-embed"
      data-testid="mealplan-google-calendar"
      referrerpolicy="no-referrer"
    />
    <p class="text-body-2 text-medium-emphasis mt-2 mb-0">
      {{ $t('calendar-sync.view-embed-hint') }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { useHouseholdSelf } from "~/composables/use-households";
import { browserTimeZone, googleCalendarEmbedUrl } from "~/composables/use-meal-plan-calendar-view";

/**
 * The household's linked Google calendar, shown through Google's own embed. The viewer's browser
 * loads it with the viewer's own Google sign-in, so who may see the calendar is Google's
 * decision; Mealie's server reads nothing from Google for this.
 */
const props = defineProps<{
  calendarId: string;
}>();

const { household } = useHouseholdSelf();

const src = computed(() =>
  googleCalendarEmbedUrl(props.calendarId, {
    timeZone: browserTimeZone(),
    firstDayOfWeek: household.value?.preferences?.firstDayOfWeek ?? undefined,
  }),
);
</script>

<style scoped>
.google-calendar-embed {
  display: block;
  width: 100%;
  height: 75vh;
  min-height: 480px;
  border: 0;
  border-radius: 4px;
  /* Google's page is always light; this keeps its corners from showing through a dark theme */
  background: #fff;
}
</style>
