import { describe, expect, test } from "vitest";
import { groupLink } from "./group-link";

describe("groupLink", () => {
  test("builds a group-scoped path when there is a slug", () => {
    expect(groupLink("home")).toBe("/g/home");
    expect(groupLink("home", "/recipes/finder")).toBe("/g/home/recipes/finder");
    expect(groupLink("home", "/cookbooks/weeknights")).toBe("/g/home/cookbooks/weeknights");
  });

  test.each([
    ["empty string", ""],
    ["null", null],
    ["undefined", undefined],
  ])("sends a visitor with no group (%s) to the login page", (_label, slug) => {
    expect(groupLink(slug)).toBe("/login");
    expect(groupLink(slug, "/recipes/finder")).toBe("/login");
  });

  test.each([
    ["", ""],
    ["", "/recipes/finder"],
    [null, "/cookbooks"],
    [undefined, "/recipes/timeline"],
  ])("never emits a slug-less /g/ path (%s, %s)", (slug, path) => {
    const href = groupLink(slug, path);
    expect(href).not.toMatch(/^\/g\/(?:$|\/)/);
    expect(href).not.toContain("//");
  });

  test("the old interpolation this replaced is what produced the 404", () => {
    // Positive control. Without it, the assertions above could pass against a
    // function that never had the defect, and prove nothing about the fix.
    const slug = "";
    expect(`/g/${slug}`).toBe("/g/");
    expect(`/g/${slug}/recipes/finder`).toBe("/g//recipes/finder");
    expect(groupLink(slug)).not.toBe("/g/");
    expect(groupLink(slug, "/recipes/finder")).not.toBe("/g//recipes/finder");
  });
});
