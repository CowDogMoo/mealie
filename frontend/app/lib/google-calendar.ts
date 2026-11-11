/**
 * Where a calendar opens in Google Calendar, given its Calendar ID. On a phone with the Google
 * Calendar app the address opens the app; anywhere else it opens the site. Someone who does not
 * have the calendar in their list yet is asked by Google whether to add it.
 */
export function googleCalendarUrl(calendarId: string): string {
  const url = new URL("https://calendar.google.com/calendar/r");
  url.searchParams.set("cid", calendarId.trim());
  return url.toString();
}
