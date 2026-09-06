import { describe, expect, test } from "vitest";
import { downvotedRecipeIds, latestFeedbackByRecipe } from "../use-users/feedback-log";
import type { UserFeedbackOut } from "~/lib/api/types/user";

function event(overrides: Partial<UserFeedbackOut>): UserFeedbackOut {
  return {
    id: "event",
    userId: "user",
    recipeId: "recipe",
    vote: "up",
    reason: null,
    scope: "recipe",
    target: null,
    note: null,
    advisory: false,
    createdAt: "2026-09-01T00:00:00",
    ...overrides,
  };
}

describe("latestFeedbackByRecipe", () => {
  test("empty log has no answers", () => {
    expect(latestFeedbackByRecipe([]).size).toBe(0);
  });

  test("keeps only the newest event per recipe", () => {
    const latest = latestFeedbackByRecipe([
      event({ id: "old-a", recipeId: "a", vote: "up", createdAt: "2026-09-01T00:00:00" }),
      event({ id: "new-a", recipeId: "a", vote: "down", createdAt: "2026-09-02T00:00:00" }),
      event({ id: "only-b", recipeId: "b", vote: "up" }),
    ]);

    expect(latest.get("a")?.id).toBe("new-a");
    expect(latest.get("b")?.id).toBe("only-b");
    expect(latest.size).toBe(2);
  });

  test("newest wins regardless of log order", () => {
    const latest = latestFeedbackByRecipe([
      event({ id: "new", vote: "down", createdAt: "2026-09-02T00:00:00" }),
      event({ id: "old", vote: "up", createdAt: "2026-09-01T00:00:00" }),
    ]);

    expect(latest.get("recipe")?.id).toBe("new");
  });

  test("a tie falls to the later element, which is the API's order", () => {
    const latest = latestFeedbackByRecipe([
      event({ id: "first", vote: "up" }),
      event({ id: "second", vote: "down" }),
    ]);

    expect(latest.get("recipe")?.id).toBe("second");
  });

  test("an event without a timestamp sorts before any dated one", () => {
    const latest = latestFeedbackByRecipe([
      event({ id: "dated", vote: "down" }),
      event({ id: "undated", vote: "up", createdAt: null }),
    ]);

    expect(latest.get("recipe")?.id).toBe("dated");
  });
});

describe("downvotedRecipeIds", () => {
  test("collects the recipes whose current answer is down", () => {
    const hidden = downvotedRecipeIds([
      event({ id: "a-down", recipeId: "a", vote: "down" }),
      event({ id: "b-up", recipeId: "b", vote: "up" }),
      event({ id: "c-neutral", recipeId: "c", vote: "neutral" }),
    ]);

    expect([...hidden]).toEqual(["a"]);
  });

  test("a later up vote lifts an earlier down vote", () => {
    const hidden = downvotedRecipeIds([
      event({ id: "down", recipeId: "a", vote: "down", createdAt: "2026-09-01T00:00:00" }),
      event({ id: "up", recipeId: "a", vote: "up", createdAt: "2026-09-02T00:00:00" }),
    ]);

    expect(hidden.size).toBe(0);
  });

  test("a later down vote hides a recipe that was once liked", () => {
    const hidden = downvotedRecipeIds([
      event({ id: "up", recipeId: "a", vote: "up", createdAt: "2026-09-01T00:00:00" }),
      event({ id: "down", recipeId: "a", vote: "down", createdAt: "2026-09-02T00:00:00" }),
    ]);

    expect(hidden.has("a")).toBe(true);
  });

  test("an empty log hides nothing", () => {
    expect(downvotedRecipeIds([]).size).toBe(0);
  });
});
