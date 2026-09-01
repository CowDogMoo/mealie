<template>
  <div :style="`height: ${height}px;`">
    <v-expand-transition>
      <v-card
        :ripple="false"
        :class="[
          isFlat ? 'mx-auto flat' : 'mx-auto',
          { 'disable-highlight': disableHighlight },
        ]"
        :style="{ cursor }"
        hover
        height="100%"
        :to="$attrs.selected ? undefined : recipeRoute"
        @click="$emit('selected')"
      >
        <v-img
          v-if="vertical"
          class="rounded-sm"
          cover
        >
          <RecipeCardImage
            tiny
            :icon-size="100"
            :slug="slug"
            :recipe-id="recipeId"
            :image-version="image"
            :height="height"
          />
        </v-img>
        <v-list-item
          lines="two"
          class="py-0"
          :class="vertical ? 'px-2' : 'px-0'"
          item-props
          height="100%"
          density="compact"
        >
          <template #prepend>
            <slot
              v-if="!vertical"
              name="avatar"
            >
              <RecipeCardImage
                tiny
                :icon-size="100"
                :slug="slug"
                :recipe-id="recipeId"
                :image-version="image"
                width="125"
                :height="height"
              />
            </slot>
          </template>
          <div class="pl-4 d-flex flex-column justify-space-between align-stretch pr-2">
            <v-list-item-title class="mt-3 mb-1 text-top text-truncate w-100">
              {{ name }}
            </v-list-item-title>
            <v-list-item-subtitle class="ma-0 text-top">
              <SafeMarkdown v-if="description" :source="description" />
              <p v-else>
                <br>
                <br>
                <br>
              </p>
            </v-list-item-subtitle>
            <div
              class="d-flex flex-nowrap justify-start ma-0 pt-2 pb-0"
              style="overflow-x: hidden; overflow-y: hidden; white-space: nowrap;"
            >
              <RecipeChips
                :truncate="true"
                :items="tags"
                :title="false"
                :limit="2"
                small
                url-prefix="tags"
                v-bind="$attrs"
              />
            </div>
          </div>
          <slot name="actions">
            <v-card-actions class="w-100 my-0 px-1 py-0">
              <RecipeFavoriteBadge
                v-if="isOwnGroup && showRecipeContent"
                :recipe-id="recipeId"
                show-always
                class="ma-0 pa-0"
              />
              <div v-else class="my-0 px-1 py-0" /> <!-- Empty div to keep the layout consistent -->
              <!--
                Measured: on the narrowest card the meal planner draws (340px, 125px of it thumbnail)
                this row has 207px, and the favourite, two thumbs and the menu take 184px of it. The
                94px star strip cannot also fit, so where feedback is on the read-only stars step
                aside on phone-width cards and come back from `sm` up, where the row has 287px and
                the compact thumbs leave 25px spare. Callers that don't opt in keep today's row.
              -->
              <RecipeCardRating
                v-if="showRecipeContent"
                :class="[{ 'pb-2': !isOwnGroup }, 'ml-n2', showFeedbackControls ? 'rating-yields-to-feedback' : '']"
                :model-value="rating"
                :recipe-id="recipeId"
              />

              <RecipeFeedbackButtons
                v-if="showFeedbackControls"
                :recipe-id="recipeId"
                :slug="slug"
                :small="smAndUp"
              />

              <!-- If we're not logged-in, no items display, so we hide this menu -->
              <!-- We also add padding to the v-rating above to compensate -->
              <RecipeContextMenu
                v-if="isOwnGroup && showRecipeContent"
                :slug="slug"
                :menu-icon="$globals.icons.dotsHorizontal"
                :name="name"
                :recipe-id="recipeId"
                class="ml-auto"
                :use-items="{
                  delete: false,
                  edit: false,
                  download: true,
                  mealplanner: true,
                  shoppingList: true,
                  print: false,
                  printPreferences: false,
                  share: true,
                }"
                @deleted="$emit('delete', slug)"
              />
            </v-card-actions>
          </slot>
        </v-list-item>
        <slot />
      </v-card>
    </v-expand-transition>
  </div>
</template>

<script setup lang="ts">
import { useDisplay } from "vuetify";
import RecipeFavoriteBadge from "./RecipeFavoriteBadge.vue";
import RecipeContextMenu from "./RecipeContextMenu/RecipeContextMenu.vue";
import RecipeCardImage from "./RecipeCardImage.vue";
import RecipeCardRating from "./RecipeCardRating.vue";
import RecipeFeedbackButtons from "./RecipeFeedbackButtons.vue";
import RecipeChips from "./RecipeChips.vue";
import { useLoggedInState } from "~/composables/use-logged-in-state";

interface Props {
  name: string;
  slug: string;
  description: string;
  rating?: number;
  image?: string;
  tags?: Array<any>;
  recipeId: string;
  vertical?: boolean;
  isFlat?: boolean;
  height?: number;
  disableHighlight?: boolean;
  showFeedback?: boolean;
}
const props = withDefaults(defineProps<Props>(), {
  rating: 0,
  image: "abc123",
  tags: () => [],
  vertical: false,
  isFlat: false,
  height: 150,
  disableHighlight: false,
  showFeedback: false,
});

defineEmits<{
  selected: [];
  delete: [slug: string];
}>();

const auth = useMealieAuth();
const { isOwnGroup } = useLoggedInState();

const route = useRoute();
const groupSlug = computed(() => route.params.groupSlug || auth.user.value?.groupSlug || "");
const showRecipeContent = computed(() => props.recipeId && props.slug);
const recipeRoute = computed<string>(() => {
  return showRecipeContent.value ? `/g/${groupSlug.value}/r/${props.slug}` : "";
});
const cursor = computed(() => showRecipeContent.value ? "pointer" : "auto");

// opt-in, and only under the same conditions that already gate the favourite badge and the
// context menu: a logged-out or cross-group viewer has nothing to vote with
const showFeedbackControls = computed(() => props.showFeedback && isOwnGroup.value && !!showRecipeContent.value);

// same breakpoint the stars use below: on a phone-width card they are hidden, which leaves room
// for thumbs the size of the heart beside them; from `sm` up the stars are back and the thumbs
// take the compact size so all four controls still fit on one row
const { smAndUp } = useDisplay();
</script>

<style scoped>
/*
  Below `sm` the action row cannot carry the star strip and the thumbs at once: the narrowest card
  the planner draws leaves the row 207px, the favourite/thumbs/menu take 184px of it and the strip
  is another 94px. The stars are display-only, so where the feedback controls are on they give way
  on phone-width cards. `!important` because the strip's own scoped rule sets `display` too, and
  Vuetify's `d-none` helper lives in a cascade layer, which loses to any unlayered component style.
*/
@media (max-width: 599.98px) {
  .rating-yields-to-feedback {
    display: none !important;
  }
}

:deep(.v-list-item__prepend) {
  height: 100%;
}
.v-mobile-img {
  padding-top: 0;
  padding-bottom: 0;
  padding-left: 0;
}
.v-card--reveal {
  align-items: center;
  bottom: 0;
  justify-content: center;
  opacity: 0.8;
  position: absolute;
  width: 100%;
}
.v-card--text-show {
  opacity: 1 !important;
}
.headerClass {
  white-space: nowrap;
  word-break: normal;
  overflow: hidden;
  text-overflow: ellipsis;
}

.text-top {
  align-self: start !important;
}

.flat,
.theme--dark .flat {
  box-shadow: none !important;
  background-color: transparent !important;
}

.disable-highlight :deep(.v-card__overlay) {
  opacity: 0 !important;
}
</style>
