<template>
  <v-container class="mx-0 my-3">
    <v-row>
      <v-col
        v-for="(day, index) in plan"
        :key="index"
        cols="12"
        sm="12"
        md="6"
        lg="4"
        xl="3"
        xxl="2"
        class="col-borders my-1 d-flex flex-column"
      >
        <v-card class="mb-2 border-left-primary rounded-sm px-2">
          <v-container class="px-0 d-flex align-center" style="height: 56px">
            <v-row no-gutters style="width: 100%;">
              <v-col cols="10" class="d-flex align-center">
                <p class="pl-2 my-1" :class="{ 'text-primary': isToday(day.date) }">
                  {{ $d(day.date, "short") }}
                </p>
              </v-col>
              <v-col class="d-flex align-center" cols="2">
                <GroupMealPlanDayContextMenu v-if="day.recipes.length" :recipes="day.recipes" />
              </v-col>
            </v-row>
          </v-container>
        </v-card>
        <VueDraggable
          v-model="dayMeals[day.key]"
          tag="div"
          v-bind="mealDragOptions"
          :sort="false"
          class="meal-drop-zone"
          :data-date="day.key"
          @end="onDrop"
        >
          <div
            v-for="(mealplan, mealIndex) in dayMeals[day.key]"
            :key="mealplan.id"
            class="meal-drag-item"
            :data-entry-id="mealplan.id"
          >
            <div
              v-if="startsSection(dayMeals[day.key], mealIndex)"
              class="pt-3 pb-1 d-flex flex-column"
            >
              <p class="text-overline my-0">
                {{ typeTitle(mealplan.entryType) }}
              </p>
            </div>

            <RecipeCardMobile
              :recipe-id="mealplan.recipe ? mealplan.recipe.id! : ''"
              class="mb-2"
              :rating="mealplan.recipe ? mealplan.recipe.rating! : 0"
              :slug="mealplan.recipe ? mealplan.recipe.slug! : ''"
              :description="mealplan.recipe ? mealplan.recipe.description! : mealplan.text!"
              :name="mealplan.recipe ? mealplan.recipe.name! : mealplan.title!"
              :image="mealplan.recipe ? mealplan.recipe.image! : undefined"
              :tags="mealplan.recipe ? mealplan.recipe.tags! : []"
              show-feedback
            />
          </div>
        </VueDraggable>
      </v-col>
    </v-row>
  </v-container>
</template>

<script setup lang="ts">
import { format, isSameDay } from "date-fns";
import { VueDraggable } from "vue-draggable-plus";

import type { PlanEntryType, ReadPlanEntry } from "~/lib/api/types/meal-plan";
import type { useMealplans } from "~/composables/use-group-mealplan";
import { usePlanTypeOptions } from "~/composables/use-group-mealplan";
import { handleMealDrop, mealDragOptions } from "~/composables/use-meal-plan-drag";
import type { MealDropEvent } from "~/composables/use-meal-plan-drag";
import GroupMealPlanDayContextMenu from "~/components/Domain/Household/GroupMealPlanDayContextMenu.vue";
import RecipeCardMobile from "~/components/Domain/Recipe/RecipeCardMobile.vue";
import type { RecipeSummary } from "~/lib/api/types/recipe";

export type MealsByDate = {
  date: Date;
  meals: ReadPlanEntry[];
};

const props = defineProps<{
  mealplans: MealsByDate[];
  actions: ReturnType<typeof useMealplans>["actions"];
}>();

type Days = {
  date: Date;
  key: string;
  recipes: RecipeSummary[];
};

const i18n = useI18n();
const planTypeOptions = usePlanTypeOptions();
const typeOrder = planTypeOptions.map(({ value }) => value);

const plan = computed<Days[]>(() => {
  return props.mealplans.map((day) => {
    return {
      date: day.date,
      key: format(day.date, "yyyy-MM-dd"),
      recipes: day.meals.flatMap(meal => meal.recipe ?? []),
    };
  });
});

// Each day's meals, grouped under a heading per meal type, as a list the drag-and-drop can edit.
// A drop moves the meal into another day's list straight away; it is rebuilt from the plan once
// the saved move comes back.
const dayMeals = reactive<{ [date: string]: ReadPlanEntry[] }>({});
watch(
  () => props.mealplans,
  (days) => {
    const keys = new Set<string>();
    for (const day of days) {
      const key = format(day.date, "yyyy-MM-dd");
      keys.add(key);
      dayMeals[key] = [...day.meals].sort(
        (a, b) => typeOrder.indexOf(a.entryType ?? "dinner") - typeOrder.indexOf(b.entryType ?? "dinner"),
      );
    }
    for (const key of Object.keys(dayMeals)) {
      if (!keys.has(key)) {
        dayMeals[key] = [];
      }
    }
  },
  { immediate: true, deep: true },
);

function startsSection(meals: ReadPlanEntry[], index: number) {
  return index === 0 || meals[index - 1]?.entryType !== meals[index]?.entryType;
}

function typeTitle(type: PlanEntryType | undefined) {
  return i18n.t(`meal-plan.${type ?? "dinner"}`);
}

function onDrop(evt: MealDropEvent) {
  return handleMealDrop(evt, props.mealplans, entry => props.actions.updateOne(entry));
}

const isToday = (date: Date) => {
  return isSameDay(date, new Date());
};
</script>

<style scoped>
/*
  RecipeCardMobile lays out a fixed-width thumbnail + a favorite/rating/menu action row
  side-by-side. Below ~320px the action row no longer fits and the "..." menu button gets
  clipped by the card's overflow:hidden. Enforcing a min-width here makes the day columns
  wrap to fewer per row instead of shrinking past that point.
*/
.col-borders {
  min-width: 340px;
}

/*
  Every day is a drop target, so an empty day still needs a body to drop on. Dragging has to
  work from a finger on a home-screen web app too: a long press on a card would otherwise select
  its text or open the iOS link preview, and neither should happen while a meal is being moved.
*/
.meal-drop-zone {
  min-height: 96px;
  border-radius: 4px;
}

.meal-drag-item {
  -webkit-touch-callout: none;
  -webkit-user-select: none;
  user-select: none;
}

.meal-drag-ghost {
  opacity: 0.4;
}

/*
  These cards pass show-feedback (as the browse grids do via RecipeCardSection), so they carry
  four more buttons: both thumbs, the die and the x. From `sm` up the card also shows the star
  strip, and favourite + stars + those four + menu need a card of ~411px; a 340px one would push
  the "..." menu onto a second line. Below `sm` the card hides the read-only stars instead, which
  the row can afford to lose, so the 340px floor above still holds there.
*/
@media (min-width: 600px) {
  .col-borders {
    min-width: 420px;
  }
}
</style>
