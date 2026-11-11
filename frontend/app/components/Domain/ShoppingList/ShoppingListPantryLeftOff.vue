<template>
  <v-alert
    v-if="leftOff.length"
    variant="tonal"
    density="compact"
    :icon="$globals.icons.foods"
    data-test="pantry-left-off"
  >
    <div class="d-flex flex-wrap align-center ga-2">
      <span>{{ $t('pantry.left-off') }}</span>
      <v-chip
        v-for="item in leftOff"
        :key="item.food.id"
        size="small"
        variant="outlined"
        :title="usedBy(item)"
        :aria-label="$t('pantry.add-back', { food: item.food.name })"
        :disabled="disabled || adding === item.food.id"
        data-test="pantry-left-off-add"
        @click="addBack(item)"
      >
        <v-icon
          start
          size="small"
        >
          {{ $globals.icons.createAlt }}
        </v-icon>
        {{ item.food.name }}
      </v-chip>
      <v-spacer />
      <v-btn
        size="small"
        variant="text"
        to="/household/pantry"
        data-test="pantry-manage"
      >
        {{ $t('pantry.manage') }}
      </v-btn>
    </div>
  </v-alert>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { useUserApi } from "~/composables/api";
import { alert } from "~/composables/use-toast";
import type { PantryLeftOffItem, ShoppingListOut } from "~/lib/api/types/household";

/**
 * What this list left off because the household usually has it.
 *
 * Pantry staples are kept off a list silently, and "usually" is not "always": the week the olive
 * oil runs out, a list that says nothing is a dinner missing an ingredient. So the list names what
 * its recipes use and it skipped, and a tap puts one back as an ordinary item. Nothing is shown
 * when nothing was left off, so a household with no staples never sees this.
 */

const props = defineProps<{
  modelValue: ShoppingListOut | null;
  disabled?: boolean;
}>();

const emit = defineEmits<{ (e: "changed"): void }>();

const i18n = useI18n();
const { $globals } = useNuxtApp();
const api = useUserApi();

const items = ref<PantryLeftOffItem[]>([]);
const adding = ref<string | null>(null);

// a food somebody has already put on the list is not "left off" any more
const leftOff = computed(() => items.value.filter(item => !item.onList));

// The page re-reads its list every few seconds. What was left off only changes when the list's
// recipes or the foods on it do, so that is what is watched, not the list object.
const signature = computed(() => {
  const list = props.modelValue;
  if (!list) {
    return "";
  }
  const recipes = (list.recipeReferences ?? []).map(ref => ref.recipeId).sort();
  const foods = (list.listItems ?? []).map(item => item.foodId ?? "").filter(Boolean).sort();
  return [list.id, recipes.join(","), foods.join(",")].join("|");
});

async function load() {
  const list = props.modelValue;
  if (!list?.id || !list.recipeReferences?.length) {
    items.value = [];
    return;
  }

  const { data } = await api.pantry.leftOff(list.id);
  // a failed read keeps what is on screen rather than implying nothing was left off
  if (data) {
    items.value = data.items ?? [];
  }
}

watch(signature, load, { immediate: true });

function usedBy(item: PantryLeftOffItem) {
  const names = (item.recipes ?? []).map(recipe => recipe.name).filter(Boolean);
  return names.length ? i18n.t("pantry.used-by", { recipes: names.join(", ") }) : "";
}

async function addBack(item: PantryLeftOffItem) {
  const list = props.modelValue;
  // a disabled chip only ignores the pointer; the offline list must not be written to at all
  if (!list?.id || adding.value || props.disabled) {
    return;
  }

  adding.value = item.food.id;
  try {
    const { data } = await api.shopping.items.createOne({
      shoppingListId: list.id,
      foodId: item.food.id,
      quantity: 0,
      checked: false,
      position: list.listItems?.length ?? 0,
    });

    if (data) {
      item.onList = true;
      alert.success(i18n.t("pantry.added-to-list", { food: item.food.name }));
      emit("changed");
    }
    else {
      alert.error(i18n.t("pantry.failed"));
    }
  }
  finally {
    adding.value = null;
  }
}
</script>
