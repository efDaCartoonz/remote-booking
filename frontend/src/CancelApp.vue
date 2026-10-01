<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { detectBrowserTimezone, formatInTimezone } from "./frame/timezone";

export interface CancellationVerificationData {
  valid: boolean;
  status: string;
  card_public_id: string | null;
  omnidesk_ticket_number: string | null;
  planned_start_at: string | null;
  planned_duration_minutes: number | null;
  card_status: string | null;
  card_status_label: string | null;
  expires_at: string | null;
  can_cancel: boolean;
}

export interface CancellationConfirmData {
  status: string;
  card_public_id: string;
  cancelled_at: string;
  idempotent: boolean;
}

const loading = ref(true);
const confirming = ref(false);
const globalError = ref<string | null>(null);
const confirmError = ref<string | null>(null);
const infoMessage = ref<string | null>(null);
const verification = ref<CancellationVerificationData | null>(null);
const confirmedResult = ref<CancellationConfirmData | null>(null);

const userTimezone = ref(detectBrowserTimezone());
let rawToken: string | null = null;

function parseAndStripToken(): string | null {
  const hash = window.location.hash || "";
  if (!hash || hash.length <= 1) {
    return null;
  }

  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  let tokenFound: string | null = null;

  try {
    const params = new URLSearchParams(raw);
    const candidate = params.get("token");
    if (candidate) {
      tokenFound = candidate.trim();
    }
  } catch {
    // Ignore URLSearchParams parsing failure
  }

  if (!tokenFound) {
    // Check if hash matches token=<val> or is plain token string
    const match = raw.match(/(?:^|&)token=([^&]+)/);
    if (match && match[1]) {
      tokenFound = decodeURIComponent(match[1]).trim();
    } else if (!raw.includes("=") && raw.trim().length > 0) {
      tokenFound = decodeURIComponent(raw).trim();
    }
  }

  // Immediately strip fragment from URL without reloading
  try {
    const cleanUrl = (window.location.pathname || "/") + (window.location.search || "");
    window.history.replaceState(null, "", cleanUrl);
  } catch {
    // Ignore history replace errors
  }

  return tokenFound;
}

function getReadableVerifyError(statusCode: number, detail?: string): string {
  if (detail === "cancellation_token_expired" || statusCode === 410) {
    return "Срок действия ссылки отмены истёк. Пожалуйста, запросите новую ссылку в переписке по обращению.";
  }
  if (detail === "cancellation_not_found" || statusCode === 404) {
    return "Ссылка отмены не найдена или уже была использована.";
  }
  if (detail === "cancellation_access_denied" || statusCode === 403) {
    return "Доступ к отмене данной записи ограничен.";
  }
  if (statusCode === 429) {
    return "Слишком много запросов. Пожалуйста, подождите перед повторной попыткой.";
  }
  if (statusCode >= 500) {
    return "Сервис временно недоступен. Попробуйте обновить страницу позже.";
  }
  return "Не удалось проверить ссылку отмены. Пожалуйста, проверьте ссылку из сообщения.";
}

function getReadableConfirmError(statusCode: number, detail?: string): string {
  if (detail === "cancellation_token_expired" || statusCode === 410) {
    return "Срок действия ссылки истёк. Запросите новую ссылку в обращении.";
  }
  if (detail === "cancellation_conflict" || statusCode === 409) {
    return "Запись уже была отменена или не может быть отменена в текущем статусе.";
  }
  if (detail === "cancellation_not_found" || statusCode === 404) {
    return "Ссылка отмены не найдена или уже была использована.";
  }
  if (detail === "cancellation_access_denied" || statusCode === 403) {
    return "Доступ запрещён.";
  }
  if (statusCode === 429) {
    return "Слишком много запросов. Пожалуйста, подождите немного.";
  }
  if (statusCode >= 500) {
    return "Сервис временно недоступен. Попробуйте подтвердить позже.";
  }
  return "Произошла ошибка при отмене записи. Попробуйте позже.";
}

async function verifyToken() {
  if (!rawToken) {
    globalError.value = "Отсутствует токен отмены. Пожалуйста, перейдите по актуальной ссылке из переписки по обращению.";
    loading.value = false;
    return;
  }

  loading.value = true;
  globalError.value = null;

  try {
    const res = await fetch("/api/v1/cancellation/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: rawToken }),
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      globalError.value = getReadableVerifyError(res.status, errData.detail);
      loading.value = false;
      return;
    }

    const data: CancellationVerificationData = await res.json();
    verification.value = { ...data };

    if (!data.valid) {
      globalError.value = "Ссылка отмены недействительна или срок её действия истёк.";
    } else if (!data.can_cancel) {
      if (data.card_status === "cancelled") {
        infoMessage.value = "Данная запись уже была отменена ранее.";
      } else {
        infoMessage.value = `Запись находится в статусе «${data.card_status_label || data.card_status}» и не может быть отменена.`;
      }
    }
  } catch {
    globalError.value = "Не удалось подключиться к серверу для проверки ссылки.";
  } finally {
    loading.value = false;
  }
}

async function handleConfirm() {
  if (confirming.value || !rawToken || !verification.value?.can_cancel) {
    return;
  }

  confirming.value = true;
  confirmError.value = null;

  try {
    const res = await fetch("/api/v1/cancellation/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: rawToken }),
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      confirmError.value = getReadableConfirmError(res.status, errData.detail);
      confirming.value = false;
      return;
    }

    const data: CancellationConfirmData = await res.json();
    confirmedResult.value = { ...data };
    if (verification.value) {
      verification.value.can_cancel = false;
      verification.value.card_status = "cancelled";
      verification.value.card_status_label = "Отменено";
    }
  } catch {
    confirmError.value = "Ошибка сети при отправке подтверждения. Проверьте соединение.";
  } finally {
    confirming.value = false;
  }
}

onMounted(() => {
  rawToken = parseAndStripToken();
  verifyToken();
});
</script>

<template>
  <div class="cancel-shell">
    <div class="cancel-card">
      <header class="cancel-header">
        <div class="cancel-eyebrow">Дистанционное подключение</div>
        <h1 class="cancel-title">
          <template v-if="verification && verification.omnidesk_ticket_number">
            Обращение #{{ verification.omnidesk_ticket_number }}
          </template>
          <template v-else>
            Отмена записи
          </template>
        </h1>
      </header>

      <!-- Loading State -->
      <div v-if="loading" class="cancel-loading">
        <div class="cancel-spinner"></div>
        <span>Проверка данных для отмены записи...</span>
      </div>

      <!-- Global Error State -->
      <div v-else-if="globalError" class="cancel-banner cancel-banner-error" role="alert">
        <strong>Ошибка:</strong> {{ globalError }}
      </div>

      <!-- Successfully Confirmed State -->
      <div v-else-if="confirmedResult" class="cancel-success-block" role="status">
        <div class="cancel-banner cancel-banner-success">
          <h2 class="cancel-success-title">Запись успешно отменена</h2>
          <p class="cancel-success-desc">
            Сеанс дистанционного подключения по обращению
            <strong v-if="verification?.omnidesk_ticket_number">#{{ verification.omnidesk_ticket_number }}</strong>
            отменён. Информация обновлена в системе.
          </p>
          <p v-if="confirmedResult.idempotent" class="cancel-idempotent-note">
            (Запись уже была отменена ранее)
          </p>
        </div>
        <div class="cancel-info-panel" v-if="confirmedResult.cancelled_at">
          <span class="cancel-info-label">Время отмены:</span>
          <span class="cancel-info-value">
            {{ formatInTimezone(confirmedResult.cancelled_at, userTimezone) }}
          </span>
        </div>
      </div>

      <!-- Cannot Cancel Info State -->
      <div v-else-if="infoMessage" class="cancel-info-block">
        <div class="cancel-banner cancel-banner-info">
          {{ infoMessage }}
        </div>
        <div v-if="verification" class="cancel-summary-panel">
          <h3 class="cancel-summary-title">Информация о записи</h3>
          <dl class="cancel-dl">
            <dt>Статус:</dt>
            <dd>
              <span class="status" :class="'status-' + verification.card_status">
                {{ verification.card_status_label || verification.card_status }}
              </span>
            </dd>
            <template v-if="verification.planned_start_at">
              <dt>Запланировано на:</dt>
              <dd>{{ formatInTimezone(verification.planned_start_at, userTimezone) }}</dd>
            </template>
            <template v-if="verification.planned_duration_minutes">
              <dt>Длительность:</dt>
              <dd>{{ verification.planned_duration_minutes }} мин</dd>
            </template>
          </dl>
        </div>
      </div>

      <!-- Verification Valid & Can Cancel: Deliberate Confirmation -->
      <div v-else-if="verification && verification.valid && verification.can_cancel" class="cancel-confirm-flow">
        <div v-if="confirmError" class="cancel-banner cancel-banner-error" role="alert">
          {{ confirmError }}
        </div>

        <section class="cancel-summary-panel">
          <h2 class="cancel-summary-title">Параметры сеанса подключения</h2>
          <dl class="cancel-dl">
            <dt>Обращение:</dt>
            <dd><strong>#{{ verification.omnidesk_ticket_number }}</strong></dd>

            <dt>Текущий статус:</dt>
            <dd>
              <span class="status" :class="'status-' + verification.card_status">
                {{ verification.card_status_label || verification.card_status }}
              </span>
            </dd>

            <template v-if="verification.planned_start_at">
              <dt>Начало сеанса:</dt>
              <dd>{{ formatInTimezone(verification.planned_start_at, userTimezone) }}</dd>
            </template>

            <template v-if="verification.planned_duration_minutes">
              <dt>Длительность:</dt>
              <dd>{{ verification.planned_duration_minutes }} мин</dd>
            </template>

            <template v-if="verification.expires_at">
              <dt>Ссылка действует до:</dt>
              <dd class="cancel-expiry">{{ formatInTimezone(verification.expires_at, userTimezone) }}</dd>
            </template>
          </dl>
        </section>

        <div class="cancel-warning-box">
          <p>
            Вы собираетесь отменить запланированный сеанс дистанционного подключения. Специалист будет уведомлен об отмене.
          </p>
        </div>

        <div class="cancel-actions">
          <button
            type="button"
            class="cancel-btn-confirm"
            :disabled="confirming"
            @click="handleConfirm"
          >
            {{ confirming ? "Отмена записи..." : "Подтвердить отмену записи" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cancel-shell {
  min-height: 100vh;
  padding: 32px 16px;
  background: var(--rdm-bg);
  display: flex;
  justify-content: center;
  align-items: flex-start;
  box-sizing: border-box;
}

.cancel-card {
  width: 100%;
  max-width: 640px;
  margin: 0 auto;
  padding: 32px;
  border: 1px solid var(--rdm-border);
  border-radius: 0;
  background: #ffffff;
  box-sizing: border-box;
}

.cancel-header {
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid #e2e8f0;
}

.cancel-eyebrow {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--rdm-text-secondary);
  margin-bottom: 6px;
}

.cancel-title {
  margin: 0;
  font-size: 24px;
  font-weight: 400;
  color: var(--rdm-text);
}

.cancel-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 48px 16px;
  color: #64748b;
  font-size: 15px;
}

.cancel-spinner {
  width: 24px;
  height: 24px;
  border: 3px solid #cbd5e1;
  border-top-color: var(--rdm-interactive);
  border-radius: 50%;
  animation: cancel-spin 0.8s linear infinite;
}

@keyframes cancel-spin {
  to {
    transform: rotate(360deg);
  }
}

.cancel-banner {
  padding: 14px 18px;
  border-radius: 0;
  margin-bottom: 20px;
  font-size: 14px;
  line-height: 1.5;
}

.cancel-banner-error {
  background: #fee2e2;
  color: #991b1b;
  border: 1px solid #fecaca;
}

.cancel-banner-success {
  background: #dcfce7;
  color: #166534;
  border: 1px solid #bbf7d0;
}

.cancel-banner-info {
  background: #e0f2fe;
  color: #075985;
  border: 1px solid #bae6fd;
}

.cancel-success-title {
  margin: 0 0 6px;
  font-size: 18px;
  font-weight: 700;
  color: #166534;
}

.cancel-success-desc {
  margin: 0;
  font-size: 14px;
  color: #1e3a2b;
}

.cancel-idempotent-note {
  margin: 6px 0 0;
  font-size: 12px;
  color: #4a6754;
}

.cancel-summary-panel {
  padding: 18px 20px;
  border: 1px solid #e2e8f0;
  border-radius: 0;
  background: var(--rdm-bg);
  margin-bottom: 20px;
}

.cancel-summary-title {
  margin: 0 0 14px;
  font-size: 16px;
  font-weight: 700;
  color: #334155;
}

.cancel-dl {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr);
  gap: 10px 16px;
  margin: 0;
  font-size: 14px;
}

.cancel-dl dt {
  color: #64748b;
  font-weight: 500;
}

.cancel-dl dd {
  margin: 0;
  color: #1e293b;
  font-weight: 500;
}

.cancel-expiry {
  color: #b45309;
}

.cancel-warning-box {
  padding: 14px 16px;
  border-radius: 0;
  background: #fcf4d6;
  border-left: 4px solid #f59e0b;
  color: #92400e;
  font-size: 13px;
  line-height: 1.5;
  margin-bottom: 24px;
}

.cancel-warning-box p {
  margin: 0;
}

.cancel-actions {
  display: flex;
  justify-content: flex-end;
}

.cancel-btn-confirm {
  width: 100%;
  padding: 13px 24px;
  font-size: 15px;
  font-weight: 650;
  color: #ffffff;
  background: var(--rdm-danger);
  border: 0;
  border-radius: 0;
  cursor: pointer;
  transition: background 0.15s ease-in-out;
}

.cancel-btn-confirm:hover:not(:disabled) {
  background: var(--rdm-danger-hover);
}

.cancel-btn-confirm:disabled {
  opacity: 0.65;
  cursor: wait;
}

.cancel-info-panel {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 0;
  font-size: 13px;
}

.cancel-info-label {
  color: #64748b;
}

.cancel-info-value {
  font-weight: 600;
  color: #1e293b;
}

@media (max-width: 600px) {
  .cancel-card {
    padding: 20px 16px;
    border-radius: 0;
  }
  .cancel-dl {
    grid-template-columns: 1fr;
    gap: 4px;
  }
  .cancel-dl dd {
    margin-bottom: 8px;
  }
}
</style>
