<template>
  <div v-if="href">
    <v-btn
      variant="outlined"
      class="rounded-xl my-1 mx-1"
      :href="href"
      target="_blank"
      rel="noopener noreferrer"
      data-testid="mealplan-open-calendar"
    >
      <v-icon start>
        {{ $globals.icons.calendar }}
      </v-icon>
      {{ $t('calendar-sync.open-calendar') }}
    </v-btn>
  </div>
</template>

<script setup lang="ts">
import { useUserApi } from "~/composables/api";
import { googleCalendarUrl } from "~/lib/google-calendar";

/**
 * A way out of the planner to the Google calendar the household's meal plan is mirrored to, which
 * is the easy place to drag a dinner to another day; the sync brings the new day back. Shows
 * nothing for a household that mirrors its plan nowhere.
 */
const api = useUserApi();
const href = ref<string | null>(null);

onMounted(async () => {
  try {
    const { data } = await api.calendarSync.get();
    href.value = data?.enabled && data.calendarId ? googleCalendarUrl(data.calendarId) : null;
  }
  catch {
    // the link is a convenience: the planner is whole without it
    href.value = null;
  }
});
</script>
