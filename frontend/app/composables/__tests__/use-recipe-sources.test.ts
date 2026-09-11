import { describe, expect, test } from "vitest";
import { domainOf, lookupMessageKey, statusColor, statusLabelKey } from "../use-recipe-sources";
import type { RecipeSourceLookupOut, RecipeSourceOut } from "~/lib/api/types/household";

function source(overrides: Partial<RecipeSourceOut> = {}): RecipeSourceOut {
  return {
    id: "source",
    domain: "example.com",
    status: "known-good",
    note: null,
    groupId: "group",
    householdId: "household",
    ...overrides,
  };
}

describe("domainOf", () => {
  test.each([
    ["https://www.BudgetBytes.com/easy-chicken/", "budgetbytes.com"],
    ["http://budgetbytes.com:8080/recipes?x=1", "budgetbytes.com"],
    ["WWW.budgetbytes.com", "budgetbytes.com"],
    ["budgetbytes.com.", "budgetbytes.com"],
    ["cooking.nytimes.com/recipes/1", "cooking.nytimes.com"],
    ["  https://seriouseats.com  ", "seriouseats.com"],
  ])("%s -> %s, matching the backend key", (input, expected) => {
    expect(domainOf(input)).toBe(expected);
  });

  test.each(["", "   ", null, undefined, "https://", "a..b", "not a url at all"])(
    "%s has no domain, so nothing is looked up",
    (input) => {
      expect(domainOf(input as string | null | undefined)).toBeNull();
    },
  );
});

describe("statusColor and statusLabelKey", () => {
  test("each status maps to its own color and label", () => {
    expect(statusColor("known-good")).toBe("success");
    expect(statusColor("caution")).toBe("warning");
    expect(statusColor("blocked")).toBe("error");
    expect(statusLabelKey("known-good")).toBe("recipe-sources.status-known-good");
    expect(statusLabelKey("caution")).toBe("recipe-sources.status-caution");
    expect(statusLabelKey("blocked")).toBe("recipe-sources.status-blocked");
  });

  test("an unlisted site is neutral and says so", () => {
    expect(statusColor(null)).toBe("grey");
    expect(statusColor(undefined)).toBe("grey");
    expect(statusLabelKey(null)).toBe("recipe-sources.status-unlisted");
  });
});

describe("lookupMessageKey", () => {
  test("nothing to say before a lookup has answered", () => {
    expect(lookupMessageKey(null)).toBeNull();
    expect(lookupMessageKey(undefined)).toBeNull();
  });

  test("a known-good site is silent", () => {
    const found: RecipeSourceLookupOut = { domain: "example.com", status: "known-good", source: source() };
    expect(lookupMessageKey(found)).toBeNull();
  });

  test("caution, blocked and unlisted each get their own sentence", () => {
    expect(lookupMessageKey({ domain: "x.com", status: "caution", source: source({ status: "caution" }) }))
      .toBe("recipe-sources.lookup-caution");
    expect(lookupMessageKey({ domain: "x.com", status: "blocked", source: source({ status: "blocked" }) }))
      .toBe("recipe-sources.lookup-blocked");
    expect(lookupMessageKey({ domain: "x.com", status: null, source: null }))
      .toBe("recipe-sources.lookup-unlisted");
  });
});
