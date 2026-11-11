<template>
  <div
    v-if="isOwnGroup"
    class="recipe-feedback-buttons d-flex align-center"
    @click.prevent
  >
    <v-btn
      :size="small ? 'x-small' : 'small'"
      :disabled="saving"
      :aria-label="$t('feedback.thumbs-up')"
      :aria-pressed="isUp"
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
        {{ isUp ? $t("feedback.undo-vote") : $t("feedback.thumbs-up") }}
      </v-tooltip>
    </v-btn>

    <v-btn
      :size="small ? 'x-small' : 'small'"
      :disabled="saving"
      :aria-label="$t('feedback.thumbs-down')"
      :aria-pressed="isDown"
      icon
      variant="text"
      @click="voteDown"
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
        {{ isDown ? $t("feedback.undo-vote") : $t("feedback.thumbs-down") }}
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

    <v-btn
      :size="small ? 'x-small' : 'small'"
      :disabled="saving"
      :aria-label="$t('feedback.dismiss')"
      data-test="dismiss"
      icon
      variant="text"
      @click="dismiss"
    >
      <v-icon :size="small ? 'small' : undefined">
        {{ $globals.icons.close }}
      </v-icon>
      <v-tooltip
        activator="parent"
        location="bottom"
      >
        {{ $t("feedback.dismiss") }}
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
import {
  DISMISS_VOTE,
  REFILL_VOTE,
  latestEventByRecipe,
  latestFeedbackByRecipe,
  latestRefillByRecipe,
} from "~/composables/use-users/feedback-log";
import type { UserFeedbackIn, UserFeedbackOut } from "~/lib/api/types/user";

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
const { userFeedback, setFeedback, deleteFeedback } = useUserSelfFeedback();

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

// Sends one event and says what came of it. The request layer answers a failed call with an
// empty response instead of throwing, so the confirmation is only for a write the server took.
async function send(payload: UserFeedbackIn, confirmation: string) {
  saving.value = true;
  try {
    const result = await setFeedback(props.slug, payload);
    if (result?.response?.status === 201) {
      alert.success(confirmation);
    }
    else {
      alert.error(i18n.t("feedback.save-failed"));
    }
  }
  finally {
    saving.value = false;
  }
}

// "Find me a new one": a request rather than a vote. It does not touch the thumbs or the star,
// but it does take this recipe out of the presser's own grids until a later thumb or an undo
// lifts it; the household's planner reads it from the feedback log and imports one new recipe in
// this one's role. Pressing it again is a fresh request, on purpose: "another one".
async function requestRefill() {
  if (saving.value) {
    return;
  }

  await send({ vote: REFILL_VOTE }, i18n.t("feedback.find-new-requested"));
}

const removed = computed(
  () => latestEventByRecipe(userFeedback.value).get(props.recipeId)?.vote === DISMISS_VOTE,
);

// The "x": take this recipe off my list without saying anything about it. No dialog, no star and
// no thumb moves, which is the point -- a thumbs down needs a reason, and "not this one" often has
// none. The recipe leaves the presser's own grids at once and the household's planner stops
// suggesting it; My Recipe Feedback lists it under "Removed", where it can be undone. Unlike the
// die, a second press says nothing new: a card that stays on screen after its x (a planned night,
// or the grid with hidden recipes shown) logs one removal however often it is pressed, so the one
// undo on that page is all it takes to bring the recipe back.
async function dismiss() {
  if (saving.value) {
    return;
  }

  if (removed.value) {
    alert.success(i18n.t("feedback.dismissed"));
    return;
  }

  await send({ vote: DISMISS_VOTE }, i18n.t("feedback.dismissed"));
}

// Pressing the thumb that is already lit takes the vote back, the way a lit thumb does
// everywhere else. It undoes the one event the thumb is showing rather than wiping the recipe's
// history, so an older vote underneath it becomes the answer again -- which is what the server
// does to the star it wrote (see `unsync_star_rating`). The die is not a toggle: pressing it
// again means "another one", so it keeps its own behaviour above.
async function undoVote() {
  const event = latest.value;
  if (!event || saving.value) {
    return;
  }

  saving.value = true;
  try {
    const result = await deleteFeedback(event.id);
    if (result?.response?.status !== 200) {
      alert.error(i18n.t("feedback.undo-failed"));
    }
  }
  finally {
    saving.value = false;
  }
}

async function voteUp() {
  if (isUp.value) {
    await undoVote();
    return;
  }

  if (saving.value) {
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

// A down vote has to say why, so casting one opens the dialog; taking one back needs nothing
// said, so the lit thumb undoes it on the spot.
async function voteDown() {
  if (isDown.value) {
    await undoVote();
    return;
  }

  dialog.value = true;
}
</script>

<style>
/*
  A card's action row once it carries these four buttons beside the favourite and the "..." menu.
  Six controls only share one row of a 300px card at 32px apiece with nothing between them, so the
  favourite and the menu come down to the size the four already are and the row gives up its gaps.
  Narrower than that, the row wraps: a control on a second line can still be pressed, one pushed
  past the card's edge cannot. The four wrap one by one rather than as a block, which keeps even a
  288px card (a 320px phone) to the two lines its fixed height has room for.
*/
.recipe-feedback-row {
  flex-wrap: wrap;
  gap: 0;
}

.recipe-feedback-row .v-btn--icon {
  width: 32px;
  height: 32px;
}

.recipe-feedback-row > .recipe-feedback-buttons {
  display: contents;
}
</style>
