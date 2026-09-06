<template>
  <v-container class="narrow-container">
    <BasePageTitle divider>
      <template #header>
        <v-img
          width="100%"
          max-height="200px"
          max-width="200px"
          src="/svgs/manage-recipes.svg"
        />
      </template>
      <template #title>
        {{ $t("feedback.my-feedback") }}
      </template>
      {{ $t("feedback.my-feedback-description") }}
    </BasePageTitle>

    <v-chip-group
      v-model="filter"
      mandatory
      selected-class="text-primary"
      class="mb-2"
    >
      <v-chip
        value="down"
        :prepend-icon="$globals.icons.thumbDown"
      >
        {{ $t("feedback.thumbs-down") }}
      </v-chip>
      <v-chip
        value="up"
        :prepend-icon="$globals.icons.thumbUp"
      >
        {{ $t("feedback.thumbs-up") }}
      </v-chip>
      <v-chip value="all">
        {{ $t("feedback.all-votes") }}
      </v-chip>
    </v-chip-group>

    <v-data-table
      :headers="headers"
      :items="rows"
      :loading="loading"
      item-value="id"
      class="elevation-0"
      :items-per-page="-1"
      hide-default-footer
    >
      <!-- a slot rather than no-data-text: Vuetify runs that prop through its locale adapter,
           which would look the already-translated sentence up as a key and warn -->
      <template #no-data>
        {{ userFeedback.length ? $t("feedback.no-feedback-for-filter") : $t("feedback.no-feedback") }}
      </template>
      <template #[`item.vote`]="{ item }">
        <v-icon
          :color="voteColor(item.vote)"
          :aria-label="voteLabel(item.vote)"
        >
          {{ voteIcon(item.vote) }}
        </v-icon>
      </template>
      <template #[`item.recipeName`]="{ item }">
        <NuxtLink
          v-if="item.route"
          :to="item.route"
        >
          {{ item.recipeName }}
        </NuxtLink>
        <span
          v-else
          class="text-disabled"
        >
          {{ $t("feedback.recipe-unavailable") }}
        </span>
      </template>
      <template #[`item.reason`]="{ item }">
        <div v-if="item.reason">
          {{ $t(`feedback.reasons.${item.reason}`) }}
        </div>
        <div
          v-if="item.scopeText"
          class="text-caption"
        >
          {{ item.scopeText }}
        </div>
        <div
          v-if="item.note"
          class="text-caption font-italic"
        >
          {{ item.note }}
        </div>
      </template>
      <template #[`item.createdAt`]="{ item }">
        <span v-if="item.createdAt">{{ $d(Date.parse(item.createdAt), "medium") }}</span>
      </template>
      <template #[`item.actions`]="{ item }">
        <v-btn
          size="small"
          variant="text"
          color="error"
          :prepend-icon="$globals.icons.undo"
          :disabled="undoing === item.id"
          :loading="undoing === item.id"
          @click="undo(item)"
        >
          {{ $t("feedback.undo-vote") }}
        </v-btn>
      </template>
    </v-data-table>
  </v-container>
</template>

<script setup lang="ts">
import { useUserApi } from "~/composables/api";
import { alert } from "~/composables/use-toast";
import { useUserSelfFeedback } from "~/composables/use-users";
import { latestFeedbackByRecipe } from "~/composables/use-users/feedback-log";
import type { UserFeedbackOut } from "~/lib/api/types/user";

type VoteFilter = "down" | "up" | "all";

interface FeedbackRow extends UserFeedbackOut {
  recipeName: string;
  route: string | null;
  scopeText: string;
}

const i18n = useI18n();
const auth = useMealieAuth();
const api = useUserApi();
const { $globals } = useNuxtApp();
const { userFeedback, deleteFeedback, ready } = useUserSelfFeedback();

useSeoMeta({
  title: i18n.t("feedback.my-feedback"),
});

const filter = ref<VoteFilter>("down");
const undoing = ref<string | null>(null);

const headers = [
  { title: i18n.t("feedback.vote"), value: "vote", sortable: false, align: "center" as const, width: "1%" },
  { title: i18n.t("general.recipe"), value: "recipeName" },
  { title: i18n.t("feedback.reason"), value: "reason" },
  { title: i18n.t("general.date"), value: "createdAt" },
  { title: "", value: "actions", sortable: false, align: "end" as const },
];

const groupSlug = computed(() => auth.user.value?.groupSlug || "");

// recipe names and slugs, looked up once per id. The feedback log only carries ids, and a recipe
// the household has since deleted stays out of this map rather than breaking the page.
const recipes = ref(new Map<string, { name: string; slug: string }>());
const loadingRecipes = ref(false);

async function loadRecipes(ids: string[]) {
  const missing = ids.filter(id => !recipes.value.has(id));
  if (!missing.length) {
    return;
  }

  loadingRecipes.value = true;
  try {
    const queryFilter = "id IN [" + missing.map(id => `"${id}"`).join(", ") + "]";
    const { data } = await api.recipes.getAll(1, -1, { queryFilter });
    const next = new Map(recipes.value);
    for (const recipe of data?.items || []) {
      if (recipe.id && recipe.slug) {
        next.set(recipe.id, { name: recipe.name || recipe.slug, slug: recipe.slug });
      }
    }
    recipes.value = next;
  }
  finally {
    loadingRecipes.value = false;
  }
}

function castAt(event: UserFeedbackOut): number {
  return event.createdAt ? Date.parse(event.createdAt) : 0;
}

// the current answer per recipe, newest first
const latest = computed(() => {
  const events = [...latestFeedbackByRecipe(userFeedback.value).values()];
  return events.sort((a, b) => castAt(b) - castAt(a));
});

watch(
  () => latest.value.map(event => event.recipeId),
  ids => loadRecipes(ids),
  { immediate: true },
);

const rows = computed<FeedbackRow[]>(() =>
  latest.value
    .filter(event => filter.value === "all" || event.vote === filter.value)
    .map((event) => {
      const recipe = recipes.value.get(event.recipeId);
      return {
        ...event,
        recipeName: recipe?.name || "",
        route: recipe && groupSlug.value ? `/g/${groupSlug.value}/r/${recipe.slug}` : null,
        scopeText: scopeText(event),
      };
    }),
);

const loading = computed(() => !ready.value || loadingRecipes.value);

// "Anything with this ingredient: fennel (advisory)"; empty for a plain recipe-scoped vote
function scopeText(event: UserFeedbackOut): string {
  if (event.scope === "recipe") {
    return "";
  }

  let text = i18n.t(`feedback.scopes.${event.scope}`);
  if (event.target) {
    text += `: ${event.target}`;
  }
  if (event.advisory) {
    text += ` (${i18n.t("feedback.advisory-tag")})`;
  }
  return text;
}

function voteIcon(vote: string) {
  if (vote === "down") {
    return $globals.icons.thumbDown;
  }
  if (vote === "up") {
    return $globals.icons.thumbUp;
  }
  return $globals.icons.minus;
}

function voteColor(vote: string) {
  if (vote === "down") {
    return "error";
  }
  if (vote === "up") {
    return "success";
  }
  return undefined;
}

function voteLabel(vote: string) {
  if (vote === "down") {
    return i18n.t("feedback.thumbs-down");
  }
  if (vote === "up") {
    return i18n.t("feedback.thumbs-up");
  }
  return i18n.t("feedback.neutral");
}

async function undo(event: FeedbackRow) {
  undoing.value = event.id;
  try {
    const result = await deleteFeedback(event.id);
    if (result?.response?.status === 200) {
      alert.success(i18n.t("feedback.vote-undone"));
    }
    else {
      alert.error(i18n.t("feedback.undo-failed"));
    }
  }
  finally {
    undoing.value = null;
  }
}
</script>
