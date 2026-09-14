<template>
  <div class="d-flex flex-column ga-2">
    <BaseDialog
      v-model="confirmOpen"
      :title="$t('shopping-list.send-to-whole-foods-question')"
      :icon="$globals.icons.cartArrowRight"
      :submit-text="$t('shopping-list.send-to-whole-foods')"
      :submit-icon="$globals.icons.cartArrowRight"
      :loading="loading"
      can-submit
      @submit="confirmSend"
    >
      <v-card-text>
        <p class="mb-2 font-weight-medium">
          {{ $t('shopping-list.cart-request-confirm-count', stillNeeded) }}
        </p>
        <p class="mb-0">
          {{ $t('shopping-list.cart-request-confirm-detail') }}
        </p>
      </v-card-text>
    </BaseDialog>

    <!-- a finished request keeps its report on screen and still offers another shop: the list moves
         on, and making somebody dismiss a report before re-sending is a step with nothing to say -->
    <v-btn
      v-if="canSend"
      class="order-last"
      variant="tonal"
      color="primary"
      data-test="cart-request-send"
      :disabled="disabled || !stillNeeded"
      :loading="loading"
      @click="confirmOpen = true"
    >
      <v-icon start>
        {{ $globals.icons.cartArrowRight }}
      </v-icon>
      {{ cartRequest ? $t('shopping-list.cart-request-send-again') : $t('shopping-list.send-to-whole-foods') }}
    </v-btn>

    <v-alert
      v-if="cartRequest"
      :type="alertType"
      variant="tonal"
      density="compact"
      :icon="statusIcon"
      data-test="cart-request-status"
    >
      <div class="d-flex flex-wrap align-center ga-2">
        <span class="flex-grow-1">
          {{ statusText }}
          <span v-if="cartRequest.result" class="d-block text-body-2">{{ cartRequest.result }}</span>
        </span>

        <v-btn
          v-if="cartRequest.status !== 'filling'"
          size="small"
          variant="text"
          data-test="cart-request-clear"
          :loading="loading"
          @click="clearRequest"
        >
          {{ cartRequest.status === 'pending'
            ? $t('shopping-list.cart-request-cancel')
            : $t('shopping-list.cart-request-dismiss') }}
        </v-btn>
      </div>
    </v-alert>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useCartRequest } from "~/composables/use-cart-request";
import { alert } from "~/composables/use-toast";
import type { ShoppingListOut } from "~/lib/api/types/household";

/**
 * "Send to Whole Foods" and everything that follows from it.
 *
 * The confirmation is the point of this component. Every other action on a shopping list is
 * reversible by tapping again; this one asks somebody to go and spend money, so it names what will
 * be bought and how many items before the request is written. It still stops short of an order --
 * the cart is filled and left for a person to check out.
 */

const props = defineProps<{
  modelValue: ShoppingListOut | null;
  disabled?: boolean;
}>();

const emit = defineEmits<{ (e: "changed"): void }>();

const i18n = useI18n();
const { $globals } = useNuxtApp();
const shoppingList = computed(() => props.modelValue);
const { cartRequest, stillNeeded, loading, send, clear } = useCartRequest(shoppingList);
const confirmOpen = ref(false);

// a shop can be started when none is owed -- including when the last one has finished, because a
// finished request is a report about a shop that is over, not a shop in progress
const canSend = computed(() => !cartRequest.value || !["pending", "filling"].includes(cartRequest.value.status));

const alertType = computed(() => {
  switch (cartRequest.value?.status) {
    case "filled":
      return "success" as const;
    case "failed":
      return "error" as const;
    default:
      return "info" as const;
  }
});

const statusIcon = computed(() => {
  switch (cartRequest.value?.status) {
    case "filled":
      return $globals.icons.cartCheck;
    case "failed":
      return $globals.icons.alertCircle;
    case "filling":
      return $globals.icons.cartArrowRight;
    default:
      return $globals.icons.clockOutline;
  }
});

const statusText = computed(() => {
  const request = cartRequest.value;
  if (!request) {
    return "";
  }

  const when = formatTime(request.status === "pending" ? request.requestedAt : request.updatedAt || request.requestedAt);
  const name = request.requestedByName;

  switch (request.status) {
    case "filling":
      return i18n.t("shopping-list.cart-request-filling", { name, time: when });
    case "filled":
      return i18n.t("shopping-list.cart-request-filled", { time: when });
    case "failed":
      return i18n.t("shopping-list.cart-request-failed", { time: when });
    default:
      return i18n.t("shopping-list.cart-request-pending", { name, time: when });
  }
});

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

async function confirmSend() {
  const result = await send();
  confirmOpen.value = false;

  // a null result means the composable refused the call -- no list, or one already in flight --
  // so there is nothing to report either way, and a success toast here would be a lie
  if (!result) {
    return;
  }

  const { error } = result;
  if (error) {
    // both refusals are things that happened between opening the dialog and confirming, and each
    // deserves its own sentence: the list emptied out, or the other person got there first. The
    // page polls every few seconds, so either way the screen is about to show what is true.
    const status = error?.response?.status;
    const message = status === 400
      ? i18n.t("shopping-list.cart-request-nothing-to-send")
      : status === 409
        ? i18n.t("shopping-list.cart-request-already-asked")
        : i18n.t("shopping-list.cart-request-failed-to-send");
    alert.error(message);
  }
  else {
    alert.success(i18n.t("shopping-list.cart-request-sent"));
  }

  emit("changed");
}

async function clearRequest() {
  const result = await clear();
  if (!result) {
    return;
  }

  const { error } = result;
  const status = error?.response?.status;
  if (status === 404) {
    // somebody else cleared it first: the thing the person wanted is already true, so refreshing
    // quietly is the honest answer rather than an error about a request that no longer exists
  }
  else if (error) {
    const message = status === 409
      ? i18n.t("shopping-list.cart-request-cannot-cancel-while-filling")
      : i18n.t("shopping-list.cart-request-failed-to-send");
    alert.error(message);
  }
  else {
    alert.success(i18n.t("shopping-list.cart-request-cleared"));
  }

  emit("changed");
}
</script>
