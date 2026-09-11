<template>
  <v-container class="narrow-container">
    <BasePageTitle divider>
      <template #header>
        <v-img
          width="100%"
          max-height="125"
          max-width="125"
          src="/svgs/manage-recipes.svg"
        />
      </template>
      <template #title>
        {{ $t('recipe-sources.title') }}
      </template>
      <v-card-text class="pb-0">
        {{ $t('recipe-sources.description') }}
      </v-card-text>
    </BasePageTitle>

    <v-form
      ref="newForm"
      @submit.prevent="addSource"
    >
      <v-card
        flat
        class="mb-4 left-border rounded"
      >
        <v-card-text class="d-flex flex-wrap ga-4 align-end">
          <v-text-field
            v-model="draft.domain"
            :label="$t('recipe-sources.domain')"
            :hint="$t('recipe-sources.domain-hint')"
            persistent-hint
            variant="underlined"
            class="flex-grow-1"
            style="min-width: 240px"
            data-testid="recipe-source-new-domain"
          />
          <v-select
            v-model="draft.status"
            :items="statusItems"
            :label="$t('recipe-sources.status')"
            variant="underlined"
            style="max-width: 200px"
            data-testid="recipe-source-new-status"
          />
          <BaseButton
            create
            type="submit"
            :disabled="!domainOf(draft.domain)"
            :loading="loading"
          />
        </v-card-text>
      </v-card>
    </v-form>

    <v-alert
      v-if="error"
      type="error"
      variant="tonal"
      class="mb-4"
      closable
      @click:close="error = ''"
    >
      {{ error }}
    </v-alert>

    <v-expansion-panels class="mt-2">
      <v-expansion-panel
        v-for="source in sources"
        :key="source.id"
        class="my-2 left-border rounded"
      >
        <v-expansion-panel-title
          disable-icon-rotate
          class="headline"
        >
          <div class="d-flex align-center ga-3">
            <v-chip
              :color="statusColor(source.status)"
              size="small"
              variant="tonal"
              label
            >
              {{ $t(statusLabelKey(source.status)) }}
            </v-chip>
            <span data-testid="recipe-source-domain-label">{{ source.domain }}</span>
            <span
              v-if="source.note"
              class="text-medium-emphasis text-truncate"
              style="max-width: 320px"
            >
              {{ source.note }}
            </span>
          </div>
          <template #actions>
            <v-btn
              size="small"
              icon
              flat
              class="ml-2"
            >
              <v-icon>
                {{ $globals.icons.edit }}
              </v-icon>
            </v-btn>
          </template>
        </v-expansion-panel-title>
        <v-expansion-panel-text>
          <HouseholdRecipeSourceEditor
            :key="source.id"
            :source="source"
            @save="saveSource"
            @delete="deleteSource"
          />
        </v-expansion-panel-text>
      </v-expansion-panel>
    </v-expansion-panels>

    <v-card-text
      v-if="!loading && sources.length === 0"
      class="text-center text-medium-emphasis"
    >
      {{ $t('recipe-sources.empty') }}
    </v-card-text>
  </v-container>
</template>

<script setup lang="ts">
import HouseholdRecipeSourceEditor from "~/components/Domain/Household/HouseholdRecipeSourceEditor.vue";
import {
  RECIPE_SOURCE_STATUSES,
  domainOf,
  statusColor,
  statusLabelKey,
  useRecipeSources,
} from "~/composables/use-recipe-sources";
import type { RecipeSourceCreate, RecipeSourceStatus } from "~/lib/api/types/household";
import { alert } from "~/composables/use-toast";

const i18n = useI18n();
const { sources, loading, actions } = useRecipeSources();

useSeoMeta({
  title: i18n.t("recipe-sources.title"),
});

const statusItems = computed(() =>
  RECIPE_SOURCE_STATUSES.map(status => ({ title: i18n.t(statusLabelKey(status)), value: status })),
);

const draft = reactive<{ domain: string; status: RecipeSourceStatus }>({ domain: "", status: "known-good" });
const error = ref("");

async function addSource() {
  error.value = "";
  const created = await actions.createOne({ domain: draft.domain, status: draft.status, note: null });
  if (created) {
    draft.domain = "";
    alert.success(i18n.t("recipe-sources.added", { domain: created.domain }));
  }
  else {
    error.value = i18n.t("recipe-sources.add-failed", { domain: domainOf(draft.domain) ?? draft.domain });
  }
}

async function saveSource(id: string, payload: RecipeSourceCreate) {
  error.value = "";
  const updated = await actions.updateOne(id, payload);
  if (updated) {
    alert.success(i18n.t("general.updated"));
  }
  else {
    error.value = i18n.t("recipe-sources.save-failed", { domain: domainOf(payload.domain) ?? payload.domain });
  }
}

async function deleteSource(id: string) {
  error.value = "";
  const deleted = await actions.deleteOne(id);
  if (deleted) {
    alert.success(i18n.t("recipe-sources.removed", { domain: deleted.domain }));
  }
}
</script>
