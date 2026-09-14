import { computed, ref, type Ref } from "vue";
import { useUserApi } from "~/composables/api";
import type { ShoppingListOut } from "~/lib/api/types/household";

/**
 * Reading and writing the "put this list in the grocery cart" request that a shopping list carries
 * in its `extras`.
 *
 * The keys and statuses below are a copy of `mealie/schema/household/cart_request.py`, which is the
 * copy of record. They are duplicated rather than generated because extras is an untyped
 * `dict[str, str]` on the wire, so no schema generator can produce them -- and they are covered by
 * a test that reads the Python module and fails if the two ever drift.
 *
 * Nothing here buys anything. Asking is a `POST`; a person still confirms before that call is made,
 * and a person still checks out.
 */

export const CART_REQUEST_EXTRAS = {
  requestId: "cartRequestId",
  status: "cartRequestStatus",
  requestedAt: "cartRequestedAt",
  requestedBy: "cartRequestedBy",
  requestedByName: "cartRequestedByName",
  itemCount: "cartRequestItemCount",
  updatedAt: "cartRequestUpdatedAt",
  result: "cartRequestResult",
} as const;

export const CART_REQUEST_STATUSES = ["pending", "filling", "filled", "failed"] as const;
export type CartRequestStatus = (typeof CART_REQUEST_STATUSES)[number];

export interface CartRequestState {
  requestId: string;
  status: CartRequestStatus;
  requestedAt: string;
  requestedByName: string;
  itemCount: number;
  updatedAt: string | null;
  result: string | null;
}

/** The cart request a list is carrying, or null when it has none or holds something unreadable. */
export function readCartRequest(shoppingList: ShoppingListOut | null | undefined): CartRequestState | null {
  const extras = (shoppingList?.extras ?? {}) as Record<string, string | undefined>;
  const status = extras[CART_REQUEST_EXTRAS.status];
  const requestId = extras[CART_REQUEST_EXTRAS.requestId];
  const requestedAt = extras[CART_REQUEST_EXTRAS.requestedAt];

  // a half-written request reads as none: the button then offers to send the list, which is both
  // the honest state and the way out of it
  if (!status || !requestId || !requestedAt) {
    return null;
  }
  if (!(CART_REQUEST_STATUSES as readonly string[]).includes(status)) {
    return null;
  }

  const itemCount = Number.parseInt(extras[CART_REQUEST_EXTRAS.itemCount] ?? "", 10);

  return {
    requestId,
    status: status as CartRequestStatus,
    requestedAt,
    requestedByName: extras[CART_REQUEST_EXTRAS.requestedByName] || extras[CART_REQUEST_EXTRAS.requestedBy] || "",
    itemCount: Number.isNaN(itemCount) ? 0 : itemCount,
    updatedAt: extras[CART_REQUEST_EXTRAS.updatedAt] ?? null,
    result: extras[CART_REQUEST_EXTRAS.result] ?? null,
  };
}

/** How many lines a shop would cover right now: everything not yet ticked off. */
export function unpurchasedCount(shoppingList: ShoppingListOut | null | undefined): number {
  return (shoppingList?.listItems ?? []).filter(item => !item.checked).length;
}

export function useCartRequest(shoppingList: Ref<ShoppingListOut | null>) {
  const api = useUserApi();
  const loading = ref(false);

  const cartRequest = computed(() => readCartRequest(shoppingList.value));
  const stillNeeded = computed(() => unpurchasedCount(shoppingList.value));

  async function send() {
    const listId = shoppingList.value?.id;
    if (!listId || loading.value) {
      return null;
    }

    loading.value = true;
    try {
      return await api.shopping.lists.requestCart(listId);
    }
    finally {
      loading.value = false;
    }
  }

  async function clear() {
    const listId = shoppingList.value?.id;
    if (!listId || loading.value) {
      return null;
    }

    loading.value = true;
    try {
      return await api.shopping.lists.clearCartRequest(listId);
    }
    finally {
      loading.value = false;
    }
  }

  return { cartRequest, stillNeeded, loading, send, clear };
}
