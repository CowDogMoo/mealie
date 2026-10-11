import { computed, ref, watch } from "vue";
import { useIntervalFn } from "@vueuse/core";
import { useUserApi } from "~/composables/api";
import type { RecipeRequestOut } from "~/lib/api/types/household";

/**
 * "Find a new recipe" from the Recipes page: ask for one, see where the ask got to, and clear it
 * once it has answered.
 *
 * The statuses and the note limit below are copies of `mealie/schema/household/recipe_request.py`,
 * which is the copy of record, and a test reads that module and fails if the two drift.
 *
 * Asking hides nothing and plans nothing. That is the difference from the "find me a new one" die
 * on a recipe card, which asks for something like *that* recipe and takes it out of the presser's
 * grids.
 */

export const RECIPE_REQUEST_STATUSES = ["pending", "filling", "filled", "failed"] as const;
export type RecipeRequestStatus = (typeof RECIPE_REQUEST_STATUSES)[number];

export const ACTIVE_RECIPE_REQUEST_STATUSES: readonly RecipeRequestStatus[] = ["pending", "filling"];

export const NOTE_MAX_LENGTH = 300;

/**
 * A finished request stays on the Recipes page this long, naming the recipe it brought in, and then
 * drops off by itself. Somebody may dismiss it sooner; nobody has to.
 */
export const FINISHED_SHOWN_FOR_MS = 7 * 24 * 60 * 60 * 1000;

/** How often the page re-reads the queue while a request is still owed. The runner ticks every five
 *  minutes, so anything much faster than this only costs requests. */
export const POLL_MS = 30_000;

export function isActive(request: Pick<RecipeRequestOut, "status">): boolean {
  return (ACTIVE_RECIPE_REQUEST_STATUSES as readonly string[]).includes(request.status ?? "");
}

/** When a request last changed, in epoch milliseconds, or NaN when it carries no readable time. */
function lastTouched(request: RecipeRequestOut): number {
  return Date.parse(request.updatedAt || request.createdAt || "");
}

/**
 * The requests worth showing: every one still owed, and every finished one from the last week. A
 * finished request with no readable time is shown rather than dropped -- it is still something the
 * household has not dismissed.
 */
export function visibleRequests(requests: RecipeRequestOut[], now: number = Date.now()): RecipeRequestOut[] {
  return requests.filter((request) => {
    if (isActive(request)) {
      return true;
    }
    const touched = lastTouched(request);
    return Number.isNaN(touched) || now - touched < FINISHED_SHOWN_FOR_MS;
  });
}

export function useRecipeRequests() {
  const api = useUserApi();
  const requests = ref<RecipeRequestOut[]>([]);
  const loading = ref(false);

  const visible = computed(() => visibleRequests(requests.value));
  const hasActive = computed(() => requests.value.some(isActive));

  async function refresh() {
    const { data } = await api.recipeRequests.getAll();
    if (data) {
      requests.value = data.requests ?? [];
    }
  }

  /** Ask for a new recipe. Returns null when a previous ask is still in flight. */
  async function ask(note: string | null = null) {
    if (loading.value) {
      return null;
    }

    loading.value = true;
    try {
      const response = await api.recipeRequests.create({ note: note?.trim() || null });
      await refresh();
      return response;
    }
    finally {
      loading.value = false;
    }
  }

  /** Cancel a waiting request, or dismiss a finished one. */
  async function clear(id: string) {
    const response = await api.recipeRequests.clear(id);
    await refresh();
    return response;
  }

  // poll only while something is owed: a page left open on Recipes all day should not keep asking
  // once every request has answered
  const { pause, resume } = useIntervalFn(refresh, POLL_MS, { immediate: false });
  watch(hasActive, active => (active ? resume() : pause()));

  return { requests, visible, hasActive, loading, refresh, ask, clear };
}
