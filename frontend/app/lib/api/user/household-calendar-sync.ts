import { BaseAPI } from "../base/base-clients";
import type { CalendarSyncResult, CalendarSyncStatus, CalendarSyncUpdate } from "~/lib/api/types/household";

const prefix = "/api";

const routes = {
  calendarSync: `${prefix}/households/calendar-sync`,
  calendarSyncNow: `${prefix}/households/calendar-sync/sync`,
};

/**
 * The household's meal plan, mirrored to one Google calendar. The plan is pushed to the calendar;
 * the one thing read back is the day of an event somebody moved in Google Calendar.
 */
export class HouseholdCalendarSyncAPI extends BaseAPI {
  /** Whether the server can reach Google, the address to share a calendar with, and how the last sync went. */
  async get() {
    return await this.requests.get<CalendarSyncStatus>(routes.calendarSync);
  }

  /** Set the calendar and the switch. Turning it on is refused (400) unless the calendar can be written to. */
  async update(payload: CalendarSyncUpdate) {
    return await this.requests.put<CalendarSyncStatus, CalendarSyncUpdate>(routes.calendarSync, payload);
  }

  /**
   * Sync now: follow any event moved to another day in Google Calendar, then make the calendar match
   * the plan. Answers with how many meals moved and how many events were added, changed and removed.
   */
  async sync() {
    return await this.requests.post<CalendarSyncResult>(routes.calendarSyncNow, {});
  }
}
