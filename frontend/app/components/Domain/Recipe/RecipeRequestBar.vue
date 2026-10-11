<template>
  <div class="mb-4" data-test="recipe-request-bar">
    <BaseDialog
      v-model="dialogOpen"
      :title="$t('recipe-request.dialog-title')"
      :icon="$globals.icons.autoFix"
      :submit-text="$t('recipe-request.submit')"
      :submit-icon="$globals.icons.autoFix"
      :loading="loading"
      can-submit
      @submit="submit"
    >
      <v-card-text>
        <p class="mb-4">
          {{ $t('recipe-request.dialog-detail') }}
        </p>
        <v-text-field
          v-model="note"
          :label="$t('recipe-request.note-label')"
          :placeholder="$t('recipe-request.note-placeholder')"
          :counter="NOTE_MAX_LENGTH"
          :maxlength="NOTE_MAX_LENGTH"
          variant="outlined"
          density="comfortable"
          hide-details="auto"
          data-test="recipe-request-note"
        />
      </v-card-text>
    </BaseDialog>

    <v-btn
      variant="tonal"
      color="primary"
      data-test="recipe-request-ask"
      :loading="loading"
      @click="openDialog"
    >
      <v-icon start>
        {{ $globals.icons.autoFix }}
      </v-icon>
      {{ $t('recipe-request.find-new') }}
    </v-btn>

    <v-alert
      v-for="request in visible"
      :key="request.id"
      :type="alertType(request)"
      :icon="statusIcon(request)"
      variant="tonal"
      density="compact"
      class="mt-2"
      data-test="recipe-request-status"
      :data-status="request.status"
    >
      <div class="d-flex flex-wrap align-center ga-2">
        <span class="flex-grow-1">
          <template v-if="request.status === 'filled' && request.recipeSlug">
            {{ $t('recipe-request.filled') }}
            <router-link
              :to="`/g/${groupSlug}/r/${request.recipeSlug}`"
              class="font-weight-medium"
              data-test="recipe-request-recipe"
            >{{ request.recipeName || request.recipeSlug }}</router-link>
          </template>
          <template v-else>
            {{ statusText(request) }}
          </template>
          <span v-if="request.note" class="d-block text-body-2">“{{ request.note }}”</span>
          <span
            v-if="request.status === 'failed' && request.result"
            class="d-block text-body-2"
          >{{ request.result }}</span>
        </span>

        <v-btn
          v-if="request.status !== 'filling'"
          size="small"
          variant="text"
          data-test="recipe-request-clear"
          @click="clearRequest(request)"
        >
          {{ request.status === 'pending' ? $t('recipe-request.cancel') : $t('recipe-request.dismiss') }}
        </v-btn>
      </div>
    </v-alert>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { NOTE_MAX_LENGTH, useRecipeRequests } from "~/composables/use-recipe-requests";
import { alert } from "~/composables/use-toast";
import type { RecipeRequestOut } from "~/lib/api/types/household";

/**
 * "Find a new recipe" on the Recipes page.
 *
 * The household's other way to get a new recipe is the die on a recipe card, which asks for one
 * *like that recipe* and hides it from the presser's grids. This one is anchored to nothing: press,
 * optionally say what you are in the mood for, and the runner adds one recipe to the box. Each
 * request stays on screen until it answers, and the answer names the recipe it brought in.
 */

defineProps<{ groupSlug: string }>();

const i18n = useI18n();
const { $globals } = useNuxtApp();
const { visible, loading, refresh, ask, clear } = useRecipeRequests();

const dialogOpen = ref(false);
const note = ref("");

onMounted(refresh);

function openDialog() {
  note.value = "";
  dialogOpen.value = true;
}

async function submit() {
  const response = await ask(note.value);

  // a null response means an earlier ask is still in flight; that one will report for both
  if (!response) {
    return;
  }

  dialogOpen.value = false;
  const { error } = response;
  if (error) {
    alert.error(
      error?.response?.status === 409
        ? i18n.t("recipe-request.too-many")
        : i18n.t("recipe-request.failed-to-send"),
    );
  }
  else {
    alert.success(i18n.t("recipe-request.requested"));
  }
}

async function clearRequest(request: RecipeRequestOut) {
  const { error } = await clear(request.id);
  const status = error?.response?.status;
  if (!error || status === 404) {
    // a 404 means somebody else cleared it first, which is what this person wanted anyway
    return;
  }
  alert.error(
    status === 409
      ? i18n.t("recipe-request.cannot-cancel-while-searching")
      : i18n.t("recipe-request.failed-to-send"),
  );
}

function alertType(request: RecipeRequestOut) {
  switch (request.status) {
    case "filled":
      return "success" as const;
    case "failed":
      return "error" as const;
    default:
      return "info" as const;
  }
}

function statusIcon(request: RecipeRequestOut) {
  switch (request.status) {
    case "filled":
      return $globals.icons.check;
    case "failed":
      return $globals.icons.alertCircle;
    case "filling":
      return $globals.icons.autoFix;
    default:
      return $globals.icons.clockOutline;
  }
}

function statusText(request: RecipeRequestOut): string {
  const name = request.requestedByName || i18n.t("recipe-request.somebody");
  switch (request.status) {
    case "filling":
      return i18n.t("recipe-request.filling", { name });
    case "failed":
      return i18n.t("recipe-request.failed");
    case "filled":
      // a filled request always names its recipe; this is only reached if that snapshot is missing
      return i18n.t("recipe-request.filled-unnamed");
    default:
      return i18n.t("recipe-request.pending", { name, time: formatTime(request.createdAt) });
  }
}

function formatTime(value: string | null | undefined): string {
  const parsed = new Date(value || "");
  return Number.isNaN(parsed.getTime())
    ? ""
    : parsed.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
</script>
