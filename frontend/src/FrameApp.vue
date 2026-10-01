<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import {
  COMMON_TIMEZONES,
  convertWallTimeToISO,
  detectBrowserTimezone,
  formatInTimezone,
  isValidTimezone,
} from "./frame/timezone";

export interface FrameCard {
  id: string;
  status: string;
  status_label: string;
  planned_start_at: string;
  planned_end_at: string;
  planned_duration_minutes: number;
  client_timezone_at_creation?: string | null;
  description?: string | null;
  available_actions: string[];
}

export interface FrameCardsData {
  omnidesk_ticket_number: string;
  can_create: boolean;
  cards: FrameCard[];
  client_name?: string | null;
  client_company_name?: string | null;
  client_contact_value?: string | null;
}

const props = defineProps<{
  initialTicketNumber?: string;
  initialCaseId?: string;
}>();
const trustedParentOrigin = import.meta.env.VITE_OMNIDESK_PARENT_ORIGIN || "https://iridi.omnidesk.ru";

// State
const loading = ref(false);
const globalError = ref<string | null>(null);
const ticketNumber = ref<string>(props.initialTicketNumber || "");
const caseIdState = ref<string>(props.initialCaseId || "");
const sessionToken = ref<string | null>(null);

const canCreate = ref(false);
const cards = ref<FrameCard[]>([]);
const clientName = ref<string | null>(null);
const clientCompanyName = ref<string | null>(null);
const clientContactPrefill = ref<string | null>(null);

// Timezone handling
const detectedTimezone = ref(detectBrowserTimezone());
const selectedTimezone = ref(detectedTimezone.value);
const isManualTimezone = ref(false);

function onSelectTimezone(e: Event) {
  const target = e.target as HTMLInputElement;
  if (target && target.value) {
    selectedTimezone.value = target.value;
    isManualTimezone.value = true;
  }
}

// Form state (UI-017)
const formDate = ref("");
const formTime = ref("");
const formDuration = ref(60);
const formContactType = ref(0); // 0 = Email, 1 = Phone
const formContactValue = ref("");
const formClientName = ref("");
const formClientCompanyName = ref("");
const formDescription = ref("");
const formSubmitting = ref(false);
const formError = ref<string | null>(null);
const formSuccess = ref<string | null>(null);

// Cancellation link request state per card
const cancellationLoading = ref<Record<string, boolean>>({});
const cancellationSuccess = ref<Record<string, string>>({});
const cancellationErrors = ref<Record<string, string>>({});

const activeCard = computed(() => {
  return cards.value.find(
    (c) => c.status !== "completed" && c.status !== "cancelled"
  );
});

function canRequestCancellation(card: FrameCard): boolean {
  return ["assigned", "confirmed", "rejected"].includes(card.status)
    && card.available_actions.includes("request_cancellation_link");
}

function getReadableError(errDetail: string): string {
  const mapping: Record<string, string> = {
    planned_start_too_soon: "Время начала должно быть не ранее чем через 2 часа от текущего момента.",
    planned_start_too_far: "Время начала должно быть не позднее чем через 14 дней.",
    planned_start_at_must_be_timezone_aware: "Некорректный формат времени.",
    active_card_exists_for_ticket: "По данному обращению уже существует активная запись.",
    ticket_not_available: "Обращение недоступно или было удалено.",
    ticket_client_mismatch: "Доступ ограничен: обращение привязано к другому пользователю.",
    ticket_client_missing: "В обращении не указан клиент.",
    frame_session_not_authenticated: "Сессия устарела. Пожалуйста, обновите страницу обращения.",
    frame_session_origin_mismatch: "Ошибка проверки источника фрейма.",
    omnidesk_ticket_not_open_after_reopen: "Не удалось открыть обращение для записи.",
    client_timezone_unknown: "Неизвестный часовой пояс.",
  };
  return mapping[errDetail] || "Произошла ошибка при выполнении операции.";
}

function getCancellationLinkError(statusCode: number, detail?: string): string {
  if (detail === "cancellation_link_throttled" || statusCode === 429) {
    return "Ссылка для отмены недавно запрашивалась. Пожалуйста, подождите перед повторным запросом.";
  }
  if (detail === "card_not_found" || statusCode === 404) {
    return "Запись не найдена.";
  }
  if (detail === "action_not_allowed_for_status" || statusCode === 409) {
    return "Запись уже отменена или не может быть отменена в текущем статусе.";
  }
  if (detail === "frame_session_not_authenticated" || statusCode === 401) {
    return "Сессия устарела. Пожалуйста, обновите страницу обращения.";
  }
  if (statusCode === 403) {
    return "Доступ к отмене данной записи ограничен.";
  }
  if (statusCode >= 500) {
    return "Сервис временно недоступен. Пожалуйста, попробуйте позже.";
  }
  return "Не удалось запросить ссылку для отмены. Попробуйте позже.";
}

async function initSession() {
  if (!ticketNumber.value || !caseIdState.value) {
    return;
  }

  loading.value = true;
  globalError.value = null;

  try {
    const res = await fetch("/api/v1/frame/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        omnidesk_ticket_number: ticketNumber.value,
        case_id: caseIdState.value,
      }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      globalError.value = getReadableError(data.detail || "session_error");
      loading.value = false;
      return;
    }

    const sessionData = await res.json();
    sessionToken.value = sessionData.token;
    await fetchCards();
  } catch {
    globalError.value = "Не удалось подключиться к серверу бронирования.";
  } finally {
    loading.value = false;
  }
}

async function fetchCards() {
  if (!sessionToken.value) return;

  try {
    const res = await fetch("/api/v1/frame/cards", {
      headers: {
        "x-rdm-frame-token": sessionToken.value,
      },
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      globalError.value = getReadableError(data.detail || "fetch_error");
      return;
    }

    const data: FrameCardsData = await res.json();
    canCreate.value = data.can_create;
    cards.value = data.cards || [];
    clientName.value = data.client_name || null;
    clientCompanyName.value = data.client_company_name || null;
    clientContactPrefill.value = data.client_contact_value || null;

    if (!formContactValue.value && data.client_contact_value) {
      formContactValue.value = data.client_contact_value;
    }
    if (!formClientName.value && data.client_name) {
      formClientName.value = data.client_name;
    }
    if (!formClientCompanyName.value && data.client_company_name) {
      formClientCompanyName.value = data.client_company_name;
    }
  } catch {
    globalError.value = "Ошибка при загрузке информации о записях.";
  }
}

async function handleRequestCancellationLink(cardId: string) {
  if (!sessionToken.value || cancellationLoading.value[cardId]) return;

  cancellationLoading.value[cardId] = true;
  cancellationErrors.value[cardId] = "";
  cancellationSuccess.value[cardId] = "";

  try {
    const res = await fetch(`/api/v1/frame/cards/${cardId}/cancellation-link`, {
      method: "POST",
      headers: {
        "x-rdm-frame-token": sessionToken.value,
      },
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      cancellationErrors.value[cardId] = getCancellationLinkError(res.status, data.detail);
      return;
    }

    cancellationSuccess.value[cardId] =
      "Запрос принят. Ссылка появится в переписке по обращению, если её удастся доставить до истечения 5 минут.";
  } catch {
    cancellationErrors.value[cardId] = "Ошибка сети при запросе ссылки отмены.";
  } finally {
    cancellationLoading.value[cardId] = false;
  }
}

async function handleCreateCard() {
  if (!sessionToken.value || !canCreate.value) return;

  formError.value = null;
  formSuccess.value = null;

  if (!formDate.value || !formTime.value) {
    formError.value = "Пожалуйста, укажите дату и время начала.";
    return;
  }
  if (!isValidTimezone(selectedTimezone.value)) {
    formError.value = "Укажите действующий часовой пояс IANA.";
    return;
  }

  let plannedStartIso: string;
  try {
    plannedStartIso = convertWallTimeToISO(
      formDate.value,
      formTime.value,
      selectedTimezone.value
    );
  } catch {
    formError.value = "Некорректная дата или время.";
    return;
  }

  formSubmitting.value = true;

  try {
    const res = await fetch("/api/v1/frame/cards", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-rdm-frame-token": sessionToken.value,
      },
      body: JSON.stringify({
        planned_start_at: plannedStartIso,
        planned_duration_minutes: Number(formDuration.value),
        client_timezone_at_creation: selectedTimezone.value,
        timezone_source_code: isManualTimezone.value ? 1 : 0,
        client_name: formClientName.value ? formClientName.value.trim() : null,
        client_company_name: formClientCompanyName.value ? formClientCompanyName.value.trim() : null,
        client_contact_type_code: formContactValue.value ? formContactType.value : null,
        client_contact_value: formContactValue.value ? formContactValue.value.trim() : null,
        description: formDescription.value ? formDescription.value.trim() : null,
      }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      formError.value = getReadableError(data.detail || "create_error");
      formSubmitting.value = false;
      return;
    }

    formSuccess.value = "Запись успешно оформлена!";
    formDate.value = "";
    formTime.value = "";
    formDescription.value = "";
    await fetchCards();
  } catch {
    formError.value = "Не удалось отправить заявку. Проверьте соединение.";
  } finally {
    formSubmitting.value = false;
  }
}

function handleMessage(event: MessageEvent) {
  if (event.source !== window.parent || event.origin !== trustedParentOrigin) return;
  if (event.data && event.data.type === "RDM_FRAME_INIT") {
    const payload = event.data.payload || {};
    if (payload.omnidesk_ticket_number && payload.case_id) {
      ticketNumber.value = String(payload.omnidesk_ticket_number);
      caseIdState.value = String(payload.case_id);
      initSession();
    }
  }
}

onMounted(() => {
  window.addEventListener("message", handleMessage);
  if (ticketNumber.value && caseIdState.value) initSession();

  // Signal parent window that frame is loaded and ready
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: "RDM_FRAME_READY" }, trustedParentOrigin);
    }
  } catch {
    // Ignore postMessage errors in sandbox/restricted environments
  }
});

onUnmounted(() => {
  window.removeEventListener("message", handleMessage);
});
</script>

<template>
  <div class="frame-shell">
    <div class="frame-card">
      <header class="frame-header">
        <div class="frame-header-main">
          <div class="frame-eyebrow">Дистанционное подключение</div>
          <h1 class="frame-title" v-if="ticketNumber">
            Обращение #{{ ticketNumber }}
          </h1>
          <h1 class="frame-title" v-else>
            Бронирование времени
          </h1>
        </div>

        <div class="frame-tz-control">
          <label class="frame-tz-label" for="tz-select">Часовой пояс:</label>
          <input
            id="tz-select"
            class="frame-tz-select"
            list="frame-timezones"
            :value="selectedTimezone"
            @change="onSelectTimezone"
          />
          <datalist id="frame-timezones">
            <option
              v-for="tz in COMMON_TIMEZONES"
              :key="tz.value"
              :value="tz.value"
            >
              {{ tz.label }}
            </option>
          </datalist>
          <small class="frame-tz-hint" v-if="!isManualTimezone">
            (определен автоматически)
          </small>
          <small class="frame-tz-hint frame-tz-manual" v-else>
            (выбран вручную)
          </small>
        </div>
      </header>

      <!-- Global Error State -->
      <div v-if="globalError" class="frame-banner frame-banner-error" role="alert">
        <strong>Ошибка:</strong> {{ globalError }}
      </div>

      <!-- Loading State -->
      <div v-else-if="loading" class="frame-loading">
        <div class="frame-spinner"></div>
        <span>Инициализация сессии и загрузка данных...</span>
      </div>

      <!-- Waiting for Handshake if no ticket info -->
      <div v-else-if="!ticketNumber" class="frame-waiting">
        <p>Ожидание параметров обращения от системы...</p>
      </div>

      <div v-else class="frame-content">
        <!-- Existing Cards List -->
        <section class="frame-section" v-if="cards.length > 0">
          <h2 class="frame-section-title">Текущие записи по обращению</h2>
          <div class="frame-cards-list">
            <article
              v-for="card in cards"
              :key="card.id"
              class="frame-ticket-card"
              :class="'status-' + card.status"
            >
              <div class="frame-card-top">
                <span class="status" :class="'status-' + card.status">
                  {{ card.status_label }}
                </span>
                <span class="frame-duration">{{ card.planned_duration_minutes }} мин</span>
              </div>

              <div class="frame-card-details">
                <div class="frame-detail-row">
                  <span class="frame-detail-label">Начало:</span>
                  <span class="frame-detail-value">
                    {{ formatInTimezone(card.planned_start_at, selectedTimezone) }}
                  </span>
                </div>
                <div class="frame-detail-row">
                  <span class="frame-detail-label">Окончание:</span>
                  <span class="frame-detail-value">
                    {{ formatInTimezone(card.planned_end_at, selectedTimezone) }}
                  </span>
                </div>
                <div class="frame-detail-row" v-if="card.description">
                  <span class="frame-detail-label">Комментарий:</span>
                  <span class="frame-detail-value">{{ card.description }}</span>
                </div>
              </div>

              <!-- Request Cancellation Link (only for assigned / confirmed / rejected) -->
              <div v-if="canRequestCancellation(card)" class="frame-card-cancel-section">
                <div
                  v-if="cancellationSuccess[card.id]"
                  class="frame-banner frame-banner-success frame-cancel-alert"
                  role="status"
                >
                  {{ cancellationSuccess[card.id] }}
                </div>
                <div
                  v-if="cancellationErrors[card.id]"
                  class="frame-banner frame-banner-error frame-cancel-alert"
                  role="alert"
                >
                  {{ cancellationErrors[card.id] }}
                </div>

                <div class="frame-cancel-action-row">
                  <p class="frame-cancel-info">
                    Для отмены записи ссылка поступит в переписку по обращению. Она действует 5 минут с момента запроса.
                  </p>
                  <button
                    type="button"
                    class="frame-btn-cancel-link"
                    :disabled="cancellationLoading[card.id]"
                    @click="handleRequestCancellationLink(card.id)"
                  >
                    {{ cancellationLoading[card.id] ? "Отправка..." : "Запросить ссылку для отмены" }}
                  </button>
                </div>
              </div>
            </article>
          </div>

          <!-- Reschedule instructions: instruct reschedule via ticket -->
          <div class="frame-notice frame-notice-reschedule">
            <p>
              <strong>Перенос записи:</strong> для изменения времени или даты сеанса подключения, пожалуйста, напишите сообщение в переписке по обращению #{{ ticketNumber }}.
            </p>
          </div>
        </section>

        <!-- Creation Form (rendered ONLY when canCreate is true) -->
        <section class="frame-section frame-create-section" v-if="canCreate">
          <h2 class="frame-section-title">Запись на дистанционное подключение</h2>
          <p class="frame-section-desc">
            Выберите удобную дату и время. Сеанс планируется не ранее чем через 2 часа от текущего момента.
          </p>

          <form @submit.prevent="handleCreateCard" class="frame-form">
            <div v-if="formError" class="frame-banner frame-banner-error" role="alert">
              {{ formError }}
            </div>
            <div v-if="formSuccess" class="frame-banner frame-banner-success" role="status">
              {{ formSuccess }}
            </div>

            <div class="frame-form-grid">
              <label class="frame-field">
                <span>Дата начала <span class="req">*</span></span>
                <input
                  type="date"
                  v-model="formDate"
                  required
                  :disabled="formSubmitting"
                />
              </label>

              <label class="frame-field">
                <span>Время начала <span class="req">*</span></span>
                <input
                  type="time"
                  v-model="formTime"
                  required
                  :disabled="formSubmitting"
                />
              </label>

              <label class="frame-field">
                <span>Длительность</span>
                <select v-model="formDuration" :disabled="formSubmitting">
                  <option :value="30">30 минут</option>
                  <option :value="60">1 час (60 мин)</option>
                  <option :value="90">1.5 часа (90 мин)</option>
                  <option :value="120">2 часа (120 мин)</option>
                </select>
              </label>

              <label class="frame-field">
                <span>Контакт для связи</span>
                <input
                  type="text"
                  v-model="formContactValue"
                  placeholder="Email или телефон"
                  :disabled="formSubmitting"
                />
              </label>

              <!-- UI-017: client name and company name -->
              <label class="frame-field">
                <span>Имя клиента</span>
                <input
                  type="text"
                  v-model="formClientName"
                  placeholder="Имя клиента"
                  :disabled="formSubmitting"
                />
              </label>

              <label class="frame-field">
                <span>Компания</span>
                <input
                  type="text"
                  v-model="formClientCompanyName"
                  placeholder="Название компании"
                  :disabled="formSubmitting"
                />
              </label>
            </div>

            <label class="frame-field frame-field-full">
              <span>Описание задачи / комментарий</span>
              <textarea
                v-model="formDescription"
                rows="3"
                placeholder="Укажите подробности или особенности подключения..."
                :disabled="formSubmitting"
              ></textarea>
            </label>

            <div class="frame-form-actions">
              <button
                type="submit"
                class="frame-btn-submit"
                :disabled="formSubmitting || !canCreate"
              >
                {{ formSubmitting ? "Оформление..." : "Записаться на сеанс" }}
              </button>
            </div>
          </form>
        </section>

        <!-- If can_create is false and active card exists -->
        <div v-else-if="activeCard" class="frame-banner frame-banner-info">
          По данному обращению уже оформлена активная запись. Новая запись будет доступна после завершения текущей.
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.frame-card-cancel-section {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid #e2e8f0;
}

.frame-cancel-alert {
  margin-bottom: 10px;
  padding: 8px 12px;
  font-size: 13px;
}

.frame-cancel-action-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.frame-cancel-info {
  margin: 0;
  font-size: 12px;
  color: #64748b;
  flex: 1 1 240px;
  line-height: 1.4;
}

.frame-btn-cancel-link {
  padding: 7px 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--rdm-danger);
  background: #ffffff;
  border: 1px solid var(--rdm-danger);
  border-radius: 0;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
}

.frame-btn-cancel-link:hover:not(:disabled) {
  background: var(--rdm-tag-red-bg);
  color: var(--rdm-danger-hover);
}

.frame-btn-cancel-link:disabled {
  opacity: 0.6;
  cursor: wait;
}
</style>
