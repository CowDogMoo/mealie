import type { RecipeSourceCreate, RecipeSourceLookupOut, RecipeSourceOut, RecipeSourceStatus } from "~/lib/api/types/household";
import { useUserApi } from "~/composables/api";

/** Every status the list knows, in the order a person would rank them. */
export const RECIPE_SOURCE_STATUSES: RecipeSourceStatus[] = ["known-good", "caution", "blocked"];

/**
 * The domain the household list is keyed on, derived the same way the backend does it:
 * lower-cased host, `www.` dropped, port and path ignored. Returns null for anything that is
 * not a URL or a bare host, so a half-typed address never triggers a lookup.
 */
export function domainOf(value: string | null | undefined): string | null {
  const raw = (value || "").trim();
  if (!raw) {
    return null;
  }

  let host: string;
  try {
    host = new URL(raw.includes("://") ? raw : `https://${raw}`).hostname;
  }
  catch {
    return null;
  }

  host = host.toLowerCase().replace(/^\.+|\.+$/g, "");
  if (host.startsWith("www.")) {
    host = host.slice(4);
  }

  if (!host || host.split(".").some(label => !label)) {
    return null;
  }

  return host;
}

/** Vuetify color for a status; `undefined` (unlisted) reads as neutral. */
export function statusColor(status: RecipeSourceStatus | null | undefined): string {
  switch (status) {
    case "known-good":
      return "success";
    case "caution":
      return "warning";
    case "blocked":
      return "error";
    default:
      return "grey";
  }
}

/** i18n key for a status label, with the unlisted case spelled out rather than left blank. */
export function statusLabelKey(status: RecipeSourceStatus | null | undefined): string {
  return status ? `recipe-sources.status-${status}` : "recipe-sources.status-unlisted";
}

/**
 * i18n key for the sentence the import page shows about a lookup result, or null when there is
 * nothing worth saying (a known-good site, or no lookup yet).
 */
export function lookupMessageKey(found: RecipeSourceLookupOut | null | undefined): string | null {
  if (!found) {
    return null;
  }
  if (!found.source) {
    return "recipe-sources.lookup-unlisted";
  }
  if (found.status === "blocked") {
    return "recipe-sources.lookup-blocked";
  }
  if (found.status === "caution") {
    return "recipe-sources.lookup-caution";
  }
  return null;
}

/** The household's whole list, with the four writes the page needs. */
export function useRecipeSources() {
  const api = useUserApi();
  const loading = ref(false);
  const sources = ref<RecipeSourceOut[]>([]);

  const actions = {
    async refresh() {
      loading.value = true;
      const { data } = await api.recipeSources.getAll(1, -1, { orderBy: "domain", orderDirection: "asc" });
      if (data?.items) {
        sources.value = data.items;
      }
      loading.value = false;
    },
    async createOne(payload: RecipeSourceCreate) {
      loading.value = true;
      const { data } = await api.recipeSources.createOne(payload);
      if (data) {
        await this.refresh();
      }
      loading.value = false;
      return data;
    },
    async updateOne(id: string, payload: RecipeSourceCreate) {
      loading.value = true;
      const { data } = await api.recipeSources.updateOne(id, payload);
      if (data) {
        await this.refresh();
      }
      loading.value = false;
      return data;
    },
    async deleteOne(id: string) {
      loading.value = true;
      const { data } = await api.recipeSources.deleteOne(id);
      if (data) {
        await this.refresh();
      }
      loading.value = false;
      return data;
    },
  };

  actions.refresh();

  return { sources, loading, actions };
}

/**
 * A debounced lookup that follows a URL as it is typed. `found` is null until the URL has a
 * usable host and the request has answered; a URL whose host has not changed is not re-asked.
 */
export function useRecipeSourceLookup(url: Ref<string | null | undefined>, debounceMs = 300) {
  const api = useUserApi();
  const found = ref<RecipeSourceLookupOut | null>(null);
  const domain = computed(() => domainOf(url.value));

  let timer: ReturnType<typeof setTimeout> | undefined;
  let asked: string | null = null;

  watch(
    domain,
    (next) => {
      if (timer) {
        clearTimeout(timer);
      }
      if (!next) {
        found.value = null;
        asked = null;
        return;
      }
      if (next === asked) {
        return;
      }
      timer = setTimeout(async () => {
        asked = next;
        const { data } = await api.recipeSources.lookup(next);
        // a slower answer to an older domain must not overwrite a newer one
        if (domain.value === next) {
          found.value = data ?? null;
        }
      }, debounceMs);
    },
    { immediate: true },
  );

  return { found, domain };
}
