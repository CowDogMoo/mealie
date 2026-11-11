import { useUserApi } from "~/composables/api";
import type { CalendarSyncStatus } from "~/lib/api/types/household";

/**
 * Which calendar the meal plan page shows.
 *
 * - `loading`: the server asks for Google's, and whether this household has one is not known yet
 * - `mealie`: Mealie's own calendar, because that is what the server is set to
 * - `google`: the household's linked Google calendar, embedded
 * - `unlinked`: the server asks for Google's but this household has none to show, so Mealie's own
 *   calendar is shown with a note saying where to link one
 */
export type MealPlanCalendar = "loading" | "mealie" | "google" | "unlinked";

/** Google's own embed page. The only Google address the meal plan page loads. */
export const GOOGLE_CALENDAR_EMBED_URL = "https://calendar.google.com/calendar/embed";

/** The viewer's own time zone, as the browser reports it. Mealie keeps none for a household. */
export function browserTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  }
  catch {
    return undefined;
  }
}

/**
 * The address of Google's embed for one calendar.
 *
 * @param firstDayOfWeek Mealie's numbering, 0 for Sunday through 6 for Saturday.
 */
export function googleCalendarEmbedUrl(
  calendarId: string,
  options: { timeZone?: string; firstDayOfWeek?: number } = {},
): string {
  const url = new URL(GOOGLE_CALENDAR_EMBED_URL);
  url.searchParams.set("src", calendarId);
  if (options.timeZone) {
    url.searchParams.set("ctz", options.timeZone);
  }
  const firstDay = options.firstDayOfWeek;
  if (firstDay !== undefined && Number.isInteger(firstDay) && firstDay >= 0 && firstDay <= 6) {
    // Google counts the same days from 1
    url.searchParams.set("wkst", String(firstDay + 1));
  }
  return url.toString();
}

/**
 * The calendar a household has linked and is keeping up to date, or null. A calendar whose mirror
 * is switched off, or that the server has no credentials to write to, would show a plan nobody is
 * updating, so it does not count.
 */
export function linkedCalendarId(status: CalendarSyncStatus | null | undefined): string | null {
  if (!status?.configured || !status.enabled) {
    return null;
  }
  return status.calendarId?.trim() || null;
}

/**
 * Decides between Mealie's own calendar and the household's Google calendar, from the server's
 * `MEAL_PLAN_CALENDAR_VIEW` setting and what the household has linked. Asks the server about the
 * household's calendar only when the setting is `google`.
 */
export function useMealPlanCalendarView() {
  const { $appInfo } = useNuxtApp();
  const wantsGoogle = $appInfo?.mealPlanCalendarView === "google";

  const status = ref<CalendarSyncStatus | null>(null);
  const loading = ref(wantsGoogle);

  const calendarId = computed(() => linkedCalendarId(status.value));

  const view = computed<MealPlanCalendar>(() => {
    if (!wantsGoogle) {
      return "mealie";
    }
    if (loading.value) {
      return "loading";
    }
    return calendarId.value ? "google" : "unlinked";
  });

  async function load() {
    try {
      const { data } = await useUserApi().calendarSync.get();
      status.value = data ?? null;
    }
    catch {
      // not knowing is the same as not linked: Mealie's own calendar is always there to show
      status.value = null;
    }
    finally {
      loading.value = false;
    }
  }

  if (wantsGoogle) {
    load();
  }

  return { view, calendarId };
}
