import { useUserApi } from "~/composables/api";
import type { UserFeedbackIn, UserFeedbackOut } from "~/lib/api/types/user";

const userFeedback = ref<UserFeedbackOut[]>([]);
const loading = ref(false);
const ready = ref(false);

export function resetUserSelfFeedback() {
  userFeedback.value = [];
  loading.value = false;
  ready.value = false;
}

export const useUserSelfFeedback = function () {
  const auth = useMealieAuth();

  async function refreshUserFeedback() {
    if (!auth.user.value || loading.value) {
      return;
    }

    loading.value = true;
    const api = useUserApi();

    const { data } = await api.users.getSelfFeedback(auth.user.value.id);
    userFeedback.value = data?.feedback || [];

    loading.value = false;
    ready.value = true;
  }

  // writes are never dropped by the in-flight guard: only the refresh above yields to one
  // already running, so a vote cast while a read is in flight is still sent.
  async function setFeedback(slug: string, payload: UserFeedbackIn) {
    const userId = auth.user.value?.id;
    if (!userId) {
      return;
    }

    loading.value = true;
    const api = useUserApi();

    await api.users.setFeedback(userId, slug, payload);

    loading.value = false;
    await refreshUserFeedback();
  }

  async function deleteFeedback(eventId: string) {
    const userId = auth.user.value?.id;
    if (!userId) {
      return;
    }

    loading.value = true;
    const api = useUserApi();

    await api.users.deleteFeedback(userId, eventId);

    loading.value = false;
    await refreshUserFeedback();
  }

  if (!ready.value) {
    refreshUserFeedback();
  }

  return {
    userFeedback,
    refreshUserFeedback,
    setFeedback,
    deleteFeedback,
    ready,
  };
};
