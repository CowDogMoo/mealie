import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";
import {
  ACTIVE_RECIPE_REQUEST_STATUSES,
  FINISHED_SHOWN_FOR_MS,
  NOTE_MAX_LENGTH,
  RECIPE_REQUEST_STATUSES,
  isActive,
  visibleRequests,
} from "../use-recipe-requests";
import type { RecipeRequestOut } from "~/lib/api/types/household";

const NOW = Date.parse("2026-10-10T18:00:00+00:00");
const DAY = 24 * 60 * 60 * 1000;

function request(status: string, touchedAgoMs: number | null, id = status): RecipeRequestOut {
  const at = touchedAgoMs === null ? null : new Date(NOW - touchedAgoMs).toISOString();
  return { id, status, groupId: "g", householdId: "h", createdAt: at, updatedAt: at };
}

describe("visibleRequests", () => {
  test("every request still owed is shown, however old", () => {
    const owed = [request("pending", 30 * DAY), request("filling", 30 * DAY)];
    expect(visibleRequests(owed, NOW)).toEqual(owed);
  });

  test("a finished request is shown for a week, then drops off by itself", () => {
    const fresh = request("filled", 6 * DAY, "fresh");
    const stale = request("filled", FINISHED_SHOWN_FOR_MS + 1, "stale");
    const failed = request("failed", DAY, "failed");
    expect(visibleRequests([fresh, stale, failed], NOW).map(r => r.id)).toEqual(["fresh", "failed"]);
  });

  test("a finished request with no readable time is kept rather than silently dropped", () => {
    const undated = request("filled", null);
    expect(visibleRequests([undated], NOW)).toEqual([undated]);
  });

  test("the update time wins over the creation time", () => {
    const old = { ...request("filled", 30 * DAY), updatedAt: new Date(NOW - DAY).toISOString() };
    expect(visibleRequests([old], NOW)).toEqual([old]);
  });
});

describe("isActive", () => {
  test.each([
    ["pending", true],
    ["filling", true],
    ["filled", false],
    ["failed", false],
  ])("%s -> %s", (status, expected) => {
    expect(isActive({ status })).toBe(expected);
  });
});

// The statuses and the note limit are copied from the Python schema, which is the copy of record.
// Reading it here means a change on one side fails this test instead of drifting quietly.
describe("agreement with mealie/schema/household/recipe_request.py", () => {
  const source = readFileSync(
    resolve(__dirname, "../../../../mealie/schema/household/recipe_request.py"),
    "utf8",
  );

  function constant(name: string): string {
    const match = source.match(new RegExp(`^${name}(?::[^=]+)? = (.+)$`, "m"));
    if (!match?.[1]) {
      throw new Error(`${name} not found in recipe_request.py`);
    }
    return match[1].trim();
  }

  test("the status words match", () => {
    const words = (name: string) => JSON.parse(constant(name)) as string;
    expect(words("RECIPE_REQUEST_PENDING")).toBe(RECIPE_REQUEST_STATUSES[0]);
    expect(words("RECIPE_REQUEST_FILLING")).toBe(RECIPE_REQUEST_STATUSES[1]);
    expect(words("RECIPE_REQUEST_FILLED")).toBe(RECIPE_REQUEST_STATUSES[2]);
    expect(words("RECIPE_REQUEST_FAILED")).toBe(RECIPE_REQUEST_STATUSES[3]);
  });

  test("the active statuses match", () => {
    expect(constant("ACTIVE_RECIPE_REQUEST_STATUSES")).toBe("(RECIPE_REQUEST_PENDING, RECIPE_REQUEST_FILLING)");
    expect(ACTIVE_RECIPE_REQUEST_STATUSES).toEqual(["pending", "filling"]);
  });

  test("the note limit matches", () => {
    expect(Number(constant("NOTE_MAX_LENGTH"))).toBe(NOTE_MAX_LENGTH);
  });
});
