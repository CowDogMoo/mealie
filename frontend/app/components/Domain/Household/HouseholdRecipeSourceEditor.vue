<template>
  <div>
    <v-card-text>
      <v-text-field
        v-model="copy.domain"
        :label="$t('recipe-sources.domain')"
        :hint="$t('recipe-sources.domain-hint')"
        persistent-hint
        variant="underlined"
        data-testid="recipe-source-domain"
      />
      <v-select
        v-model="copy.status"
        :items="statusItems"
        :label="$t('recipe-sources.status')"
        variant="underlined"
        class="mt-2"
        data-testid="recipe-source-status-select"
      />
      <v-textarea
        v-model="copy.note"
        :label="$t('recipe-sources.note')"
        variant="underlined"
        rows="2"
        auto-grow
      />
    </v-card-text>
    <v-card-actions class="py-0 justify-end">
      <BaseButtonGroup
        :buttons="[
          {
            icon: $globals.icons.delete,
            text: $t('general.delete'),
            event: 'delete',
          },
          {
            icon: $globals.icons.save,
            text: $t('general.save'),
            event: 'save',
          },
        ]"
        @delete="$emit('delete', source.id)"
        @save="handleSave"
      />
    </v-card-actions>
  </div>
</template>

<script setup lang="ts">
import type { RecipeSourceCreate, RecipeSourceOut } from "~/lib/api/types/household";
import { RECIPE_SOURCE_STATUSES, statusLabelKey } from "~/composables/use-recipe-sources";

const props = defineProps<{
  source: RecipeSourceOut;
}>();

const emit = defineEmits<{
  delete: [id: string];
  save: [id: string, payload: RecipeSourceCreate];
}>();

const i18n = useI18n();

const copy = ref<RecipeSourceCreate>({
  domain: props.source.domain,
  status: props.source.status ?? "known-good",
  note: props.source.note ?? "",
});

const statusItems = computed(() =>
  RECIPE_SOURCE_STATUSES.map(status => ({ title: i18n.t(statusLabelKey(status)), value: status })),
);

function handleSave() {
  emit("save", props.source.id, { ...copy.value, note: copy.value.note || null });
}
</script>
