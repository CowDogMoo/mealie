<template>
  <v-container class="narrow-container">
    <BasePageTitle divider>
      <template #header>
        <v-img
          width="100%"
          max-height="125"
          max-width="125"
          src="/svgs/manage-tasks.svg"
        />
      </template>
      <template #title>
        {{ $t('calendar-sync.title') }}
      </template>
      <v-card-text class="pb-0">
        {{ $t('calendar-sync.description') }}
      </v-card-text>
    </BasePageTitle>

    <v-alert
      v-if="status && !status.configured"
      type="info"
      variant="tonal"
      data-testid="calendar-sync-not-configured"
    >
      {{ $t('calendar-sync.not-configured', { setting: CREDENTIALS_SETTING }) }}
    </v-alert>

    <template v-else-if="status">
      <v-card
        flat
        class="mb-4 left-border rounded"
      >
        <v-card-text>
          <h3 class="text-subtitle-1 mb-2">
            {{ $t('calendar-sync.setup-heading') }}
          </h3>
          <div
            v-if="status.accountEmail"
            class="d-flex flex-wrap align-center ga-2 mb-2"
          >
            <span class="text-medium-emphasis">{{ $t('calendar-sync.account-label') }}</span>
            <code data-testid="calendar-sync-account">{{ status.accountEmail }}</code>
            <AppButtonCopy
              :copy-text="status.accountEmail"
              btn-class="ml-n1"
            />
          </div>
          <ol
            class="pl-6"
            data-testid="calendar-sync-steps"
          >
            <li>{{ $t('calendar-sync.step-create') }}</li>
            <li>{{ $t('calendar-sync.step-share', { email: status.accountEmail }) }}</li>
            <li>{{ $t('calendar-sync.step-id') }}</li>
          </ol>
          <p
            class="text-body-2 text-medium-emphasis mt-3 mb-0"
            data-testid="calendar-sync-server-wide"
          >
            {{ $t('calendar-sync.server-wide-note') }}
          </p>
        </v-card-text>
      </v-card>

      <v-form @submit.prevent="save">
        <v-card
          flat
          class="mb-4 left-border rounded"
        >
          <v-card-text>
            <v-text-field
              v-model="draft.calendarId"
              :label="$t('calendar-sync.calendar-id')"
              :hint="$t('calendar-sync.calendar-id-hint')"
              persistent-hint
              variant="underlined"
              autocomplete="off"
              :disabled="!canManage || saving"
              data-testid="calendar-sync-id"
            />
            <v-switch
              v-model="draft.enabled"
              :label="$t('calendar-sync.enable')"
              color="primary"
              hide-details
              :disabled="!canManage || saving"
              data-testid="calendar-sync-enabled"
            />
            <p
              v-if="!canManage"
              class="text-body-2 text-medium-emphasis mt-2"
              data-testid="calendar-sync-needs-permission"
            >
              {{ $t('calendar-sync.needs-permission') }}
            </p>
            <v-alert
              v-if="saveError"
              type="error"
              variant="tonal"
              class="mt-3"
              closable
              data-testid="calendar-sync-save-error"
              @click:close="saveError = ''"
            >
              {{ saveError }}
            </v-alert>
          </v-card-text>
          <v-card-actions class="px-4 pb-4">
            <BaseButton
              save
              type="submit"
              :loading="saving"
              :disabled="!canManage"
              data-testid="calendar-sync-save"
            />
            <v-spacer />
            <BaseButton
              color="info"
              :loading="syncing"
              :disabled="!status.enabled || syncing"
              data-testid="calendar-sync-now"
              @click="syncNow"
            >
              <template #icon>
                {{ $globals.icons.refresh }}
              </template>
              {{ $t('calendar-sync.sync-now') }}
            </BaseButton>
          </v-card-actions>
        </v-card>
      </v-form>

      <v-alert
        v-if="status.lastError"
        type="error"
        variant="tonal"
        class="mb-3"
        data-testid="calendar-sync-last-error"
      >
        {{ $t('calendar-sync.last-error', { error: status.lastError }) }}
      </v-alert>
      <p
        class="text-body-2 text-medium-emphasis"
        data-testid="calendar-sync-last-synced"
      >
        {{
          status.lastSyncedAt
            ? $t('calendar-sync.last-synced', { when: $d(Date.parse(status.lastSyncedAt), 'long') })
            : $t('calendar-sync.never-synced')
        }}
      </p>
      <p class="text-body-2 text-medium-emphasis">
        {{ $t('calendar-sync.sync-hint') }}
      </p>
      <p
        class="text-body-2 text-medium-emphasis"
        data-testid="calendar-sync-left-behind"
      >
        {{ $t('calendar-sync.left-behind-note') }}
      </p>
    </template>
  </v-container>
</template>

<script setup lang="ts">
import { useUserApi } from "~/composables/api";
import { alert } from "~/composables/use-toast";
import type { CalendarSyncStatus } from "~/lib/api/types/household";

/** The server setting that holds the Google credential. Named on the page, never translated. */
const CREDENTIALS_SETTING = "GOOGLE_CALENDAR_CREDENTIALS";

const i18n = useI18n();
const api = useUserApi();
const auth = useMealieAuth();

useSeoMeta({
  title: i18n.t("calendar-sync.title"),
});

const status = ref<CalendarSyncStatus | null>(null);
const draft = reactive({ calendarId: "", enabled: false });
const saving = ref(false);
const syncing = ref(false);
const saveError = ref("");

// any member may look and sync; only someone who manages the household may change the settings,
// which is the rule the API enforces
const canManage = computed(() => !!auth.user.value?.canManageHousehold);

/** Show what the server holds, in the form too. For the first load and for a save's own answer. */
function show(next: CalendarSyncStatus) {
  status.value = next;
  draft.calendarId = next.calendarId ?? "";
  draft.enabled = !!next.enabled;
}

/** Re-read how the mirror stands without touching the form, which may hold unsaved typing. */
async function refreshStatus() {
  const { data } = await api.calendarSync.get();
  if (data) {
    status.value = data;
  }
}

/** The sentence the server sent with a refusal, e.g. which address the calendar must be shared with. */
function refusalOf(error: unknown): string {
  const message = (error as { response?: { data?: { detail?: { message?: unknown } } } } | null)?.response?.data
    ?.detail?.message;
  return typeof message === "string" ? message : "";
}

async function save() {
  if (!canManage.value || saving.value) {
    return;
  }

  saving.value = true;
  saveError.value = "";
  try {
    const { data, error } = await api.calendarSync.update({
      calendarId: draft.calendarId.trim() || null,
      enabled: draft.enabled,
    });
    if (!data) {
      // kept on the page, not only in a toast: it says what to change in Google Calendar, and
      // somebody has to go and do that before trying again
      saveError.value = refusalOf(error) || i18n.t("calendar-sync.save-failed");
      return;
    }

    show(data);
    alert.success(i18n.t("calendar-sync.saved"));
  }
  finally {
    saving.value = false;
  }
}

async function syncNow() {
  if (syncing.value) {
    return;
  }

  syncing.value = true;
  try {
    const { data, error } = await api.calendarSync.sync();
    if (data) {
      const counts = {
        created: data.created ?? 0,
        updated: data.updated ?? 0,
        deleted: data.deleted ?? 0,
        // meals whose event somebody moved to another day in Google Calendar
        moved: data.moved ?? 0,
      };
      const changed = counts.created + counts.updated + counts.deleted;
      if (counts.moved) {
        alert.success(i18n.t("calendar-sync.synced-moved", counts));
      }
      else {
        alert.success(changed ? i18n.t("calendar-sync.synced", counts) : i18n.t("calendar-sync.synced-no-changes"));
      }
    }
    else if (!refusalOf(error)) {
      // a refusal with a sentence of its own has already been shown by the request layer
      alert.error(i18n.t("calendar-sync.sync-failed"));
    }

    // either way, the server now knows when the last sync was and why it failed, if it did
    await refreshStatus();
  }
  finally {
    syncing.value = false;
  }
}

onMounted(async () => {
  const { data } = await api.calendarSync.get();
  if (data) {
    show(data);
  }
});
</script>
