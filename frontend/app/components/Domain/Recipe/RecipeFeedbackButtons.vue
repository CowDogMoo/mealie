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
import { useUserSelfFeedback } from "~/composables/use-users";
import type { UserFeedbackOut } from "~/lib/api/types/user";

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

function castAt(event: UserFeedbackOut): number {
  return event.createdAt ? Date.parse(event.createdAt) : 0;
}

// the log keeps every vote this person ever cast on this recipe; only the last one is their
// current answer. Ties fall to the later element, which is the order the API returns them in.
const latest = computed<UserFeedbackOut | null>(() => {
  const mine = userFeedback.value.filter(event => event.recipeId === props.recipeId);
  if (!mine.length) {
    return null;
  }

  return mine.reduce((newest, event) => (castAt(event) >= castAt(newest) ? event : newest));
});

const isUp = computed(() => latest.value?.vote === "up");
const isDown = computed(() => latest.value?.vote === "down");

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
