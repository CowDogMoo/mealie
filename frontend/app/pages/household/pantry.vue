<template>
  <v-container class="narrow-container">
    <BasePageTitle divider>
      <template #header>
        <v-img
          width="100%"
          max-height="125"
          max-width="125"
          src="/svgs/shopping-cart.svg"
        />
      </template>
      <template #title>
        {{ $t('pantry.title') }}
      </template>
      <v-card-text class="pb-0">
        {{ $t('pantry.description') }}
      </v-card-text>
    </BasePageTitle>

    <v-card
      flat
      class="mb-4 left-border rounded"
    >
      <v-card-text>
        <v-autocomplete
          v-model="picked"
          :items="unmarkedFoods"
          item-title="name"
          item-value="id"
          :label="$t('pantry.add-label')"
          :loading="saving"
          :disabled="saving"
          variant="underlined"
          hide-details
          auto-select-first
          clearable
          data-testid="pantry-add"
          @update:model-value="onPicked"
        />
      </v-card-text>
    </v-card>

    <section data-testid="pantry-staples">
      <h3 class="text-subtitle-1 mb-2">
        {{ $t('pantry.staples-heading', { count: staples.length }) }}
      </h3>
      <div
        v-if="staples.length"
        class="d-flex flex-wrap ga-2"
      >
        <v-chip
          v-for="food in staples"
          :key="food.id"
          closable
          :close-label="$t('pantry.remove', { food: food.name })"
          :disabled="saving"
          data-testid="pantry-staple"
          @click:close="remove(food)"
        >
          {{ food.name }}
        </v-chip>
      </div>
      <p
        v-else-if="!loading"
        class="text-medium-emphasis"
      >
        {{ $t('pantry.empty') }}
      </p>
    </section>

    <section
      v-if="suggestions.length"
      class="mt-6"
      data-testid="pantry-suggestions"
    >
      <h3 class="text-subtitle-1 mb-1">
        {{ $t('pantry.suggestions-heading') }}
      </h3>
      <p class="text-body-2 text-medium-emphasis mb-2">
        {{ $t('pantry.suggestions-description') }}
      </p>
      <div class="d-flex flex-wrap ga-2">
        <v-chip
          v-for="food in suggestions"
          :key="food.id"
          variant="outlined"
          :title="food.because ? $t('pantry.suggestion-because', { staple: food.because }) : ''"
          :disabled="saving"
          data-testid="pantry-suggestion"
          @click="add(food.id)"
        >
          <v-icon
            start
            size="small"
          >
            {{ $globals.icons.createAlt }}
          </v-icon>
          {{ food.name }}
        </v-chip>
      </div>
    </section>
  </v-container>
</template>

<script setup lang="ts">
import { useUserApi } from "~/composables/api";
import { useFoodStore } from "~/composables/store";
import { alert } from "~/composables/use-toast";
import type { PantryFood, PantrySuggestion } from "~/lib/api/types/household";

const i18n = useI18n();
const api = useUserApi();
const { store: foods } = useFoodStore();

useSeoMeta({
  title: i18n.t("pantry.title"),
});

const staples = ref<PantryFood[]>([]);
const suggestions = ref<PantrySuggestion[]>([]);
const loading = ref(true);
const saving = ref(false);
const picked = ref<string | null>(null);

const unmarkedFoods = computed(() => {
  const marked = new Set(staples.value.map(food => food.id));
  return foods.value.filter(food => !marked.has(food.id));
});

async function refresh() {
  const { data } = await api.pantry.get();
  if (data) {
    staples.value = data.staples ?? [];
    suggestions.value = data.suggestions ?? [];
  }
  loading.value = false;
}

async function add(foodId: string) {
  saving.value = true;
  try {
    const { data } = await api.pantry.mark(foodId);
    if (!data) {
      alert.error(i18n.t("pantry.failed"));
      return;
    }

    // marking salt also marks kosher salt; say so, because those were not what was tapped
    const forms = (data.alsoMarked ?? []).map(food => food.name);
    alert.success(
      forms.length
        ? i18n.t("pantry.added-with-forms", { food: data.food.name, forms: forms.join(", ") })
        : i18n.t("pantry.added", { food: data.food.name }),
    );
    await refresh();
  }
  finally {
    saving.value = false;
  }
}

async function onPicked(foodId: string | null) {
  if (!foodId) {
    return;
  }
  await add(foodId);
  picked.value = null;
}

async function remove(food: PantryFood) {
  saving.value = true;
  try {
    const { data } = await api.pantry.unmark(food.id);
    if (!data) {
      alert.error(i18n.t("pantry.failed"));
      return;
    }
    alert.success(i18n.t("pantry.removed", { food: food.name }));
    await refresh();
  }
  finally {
    saving.value = false;
  }
}

onMounted(refresh);
</script>
