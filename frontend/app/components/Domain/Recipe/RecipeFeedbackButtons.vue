<template>
  <div
    v-if="isOwnGroup"
    class="d-flex align-center"
    @click.prevent
  >
    <v-btn
      :size="small ? 'x-small' : 'small'"
      :disabled="saving"
      :aria-label="$t('feedback.thumbs-up')"
      icon
      variant="text"
      @click="voteUp"
    >
      <v-icon
        :size="small ? 'small' : undefined"
        :color="isUp ? 'success' : undefined"
      >
        {{ isUp ? $globals.icons.thumbUp : $globals.icons.thumbUpOutline }}
      </v-icon>
      <v-tooltip
        activator="parent"
        location="bottom"
      >
        {{ $t("feedback.thumbs-up") }}
      </v-tooltip>
    </v-btn>

    <v-btn
      :size="small ? 'x-small' : 'small'"
      :disabled="saving"
      :aria-label="$t('feedback.thumbs-down')"
      icon
      variant="text"
      @click="dialog = true"
    >
      <v-icon
        :size="small ? 'small' : undefined"
        :color="isDown ? 'error' : undefined"
      >
        {{ isDown ? $globals.icons.thumbDown : $globals.icons.thumbDownOutline }}
      </v-icon>
      <v-tooltip
        activator="parent"
        location="bottom"
      >
        {{ $t("feedback.thumbs-down") }}
      </v-tooltip>
    </v-btn>

    <v-btn
      :size="small ? 'x-small' : 'small'"
      :disabled="saving"
      :aria-label="$t('feedback.find-new')"
      data-test="find-new"
      icon
      variant="text"
      @click="requestRefill"
    >
      <v-icon
        :size="small ? 'small' : undefined"
        :color="requested ? 'primary' : undefined"
      >
        {{ $globals.icons.diceMultiple }}
      </v-icon>
      <v-tooltip
        activator="parent"
        location="bottom"
      >
        {{ requested ? $t("feedback.find-new-requested") : $t("feedback.find-new") }}
      </v-tooltip>
    </v-btn>

    <RecipeFeedbackDialog
      v-model="dialog"
      :recipe-id="recipeId"
      :slug="slug"
    />
  </div>
</template>

<script setup lang="ts">
import RecipeFeedbackDialog from "./RecipeFeedbackDialog.vue";
import { useLoggedInState } from "~/composables/use-logged-in-state";
import { alert } from "~/composables/use-toast";
import { useUserSelfFeedback } from "~/composables/use-users";
import { REFILL_VOTE, latestFeedbackByRecipe, latestRefillByRecipe } from "~/composables/use-users/feedback-log";
import type { UserFeedbackOut } from "~/lib/api/types/user";

/** A refill asked for this recently still shows on the die, so a person can see it was heard. */
const REQUESTED_RECENTLY_MS = 24 * 60 * 60 * 1000;

interface Props {
  recipeId: string;
  slug: string;
  small?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  small: false,
});

const { isOwnGroup } = useLoggedInState();
const { userFeedback, setFeedback } = useUserSelfFeedback();

const dialog = ref(false);
const saving = ref(false);

const latest = computed<UserFeedbackOut | null>(
  () => latestFeedbackByRecipe(userFeedback.value).get(props.recipeId) ?? null,
);

const isUp = computed(() => latest.value?.vote === "up");
const isDown = computed(() => latest.value?.vote === "down");

const i18n = useI18n();

const requested = computed(() => {
  const refill = latestRefillByRecipe(userFeedback.value).get(props.recipeId);
  if (!refill?.createdAt) {
    return false;
  }
  return Date.now() - Date.parse(refill.createdAt) < REQUESTED_RECENTLY_MS;
});

// "Find me a new one": a request rather than a vote. It does not touch the thumbs, the star, or
// what is hidden; the household's planner reads it from the feedback log and imports one new
// recipe in this one's role. Pressing it again is a fresh request, on purpose: "another one".
async function requestRefill() {
  if (saving.value) {
    return;
  }

  saving.value = true;
  try {
    await setFeedback(props.slug, { vote: REFILL_VOTE });
    alert.success(i18n.t("feedback.find-new-requested"));
  }
  finally {
    saving.value = false;
  }
}

async function voteUp() {
  // an up vote carries no reason, so casting it again would only add a duplicate to the log
  if (isUp.value || saving.value) {
    return;
  }

  saving.value = true;
  try {
    await setFeedback(props.slug, { vote: "up" });
  }
  finally {
    saving.value = false;
  }
}
</script>

<style lang="scss" scoped></style>
