<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

type Role = { id: number; name: string };
type User = { id: number; username: string; full_name: string; roles: Role[] };
type Card = {
  id: string;
  number: string;
  omnidesk_ticket_number: string;
  status: string;
  status_label: string;
  planned_start_at: string;
  planned_end_at: string;
  planned_duration_minutes: number;
  l1_owner_id: number | null;
  l1_owner_name: string | null;
  l2_engineer_id: number | null;
  l2_engineer_name: string | null;
  client_informed: boolean;
  criticality_code: number;
  urgency_code: number;
  overdue_flag: boolean;
  out_of_hours_flag: boolean;
  retroactive_flag: boolean;
  description: string | null;
  result_code: number | null;
  engineer_report: string | null;
  actual_start_at: string | null;
  actual_end_at: string | null;
  created_at: string;
  updated_at: string;
};
type HistoryEntry = { event_label: string; actor_label: string; created_at: string };
type ApiError = Error & { status: number; detail?: unknown };
type ManagerCard = { public_id: string; number: string; omnidesk_ticket_number: string; status: string; status_label: string; planned_start_at: string; planned_end_at: string; planned_duration_minutes: number; l1_owner_name: string | null; l2_engineer_name: string | null; urgent: boolean; overdue: boolean; out_of_hours: boolean };
type ManagerData = { summary: { assigned: number; confirmed: number; rejected: number; overdue: number }; items: ManagerCard[]; limit: number };

const RETURN_TO_KEY = "rdm.return_to";
const user = ref<User | null>(null);
const card = ref<Card | null>(null);
const history = ref<HistoryEntry[]>([]);
const busy = ref(true);
const errorStatus = ref<number | null>(null);
const historyError = ref("");
const actionError = ref("");
const actionBusy = ref("");
const username = ref("");
const password = ref("");
const loginBusy = ref(false);
const loginError = ref("");
const rejectionReason = ref("");
const rescheduleStart = ref("");
const rescheduleDuration = ref(60);
const rescheduleDescription = ref("");
const cardId = computed(() => location.pathname.match(/^\/cards\/([^/]+)\/?$/)?.[1]);
const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Moscow";
const managerPath = location.pathname === "/manager";
const manager = ref<ManagerData | null>(null);
const managerStatus = ref("");
const managerFrom = ref("");
const managerTo = ref("");
const managerError = ref("");
const managerView = ref<"list" | "calendar">("list");
const calendarMode = ref<"day" | "week">("week");
const managerLoading = ref(false);

type ManagerPeriod = { periodFrom: string | null; periodTo: string | null };

function localDateAt(dateValue: string, dayOffset = 0): Date {
  const [year, month, day] = dateValue.split("-").map(Number);
  return new Date(year, month - 1, day + dayOffset, 0, 0, 0, 0);
}

function managerPeriod(from: string, to: string): ManagerPeriod | string {
  if (from && to && localDateAt(to) < localDateAt(from)) return "Дата окончания не может быть раньше даты начала.";
  return { periodFrom: from ? localDateAt(from).toISOString() : null, periodTo: to ? localDateAt(to, 1).toISOString() : null };
}

const hasL1Role = computed(() => hasRole(1));
const hasL2Role = computed(() => hasRole(2));
const canDecideAsL2 = computed(
  () =>
    !!user.value &&
    !!card.value &&
    hasL2Role.value &&
    card.value.status === "assigned" &&
    card.value.l2_engineer_id === user.value.id,
);
const canFollowUpAsL1 = computed(
  () =>
    !!user.value &&
    !!card.value &&
    hasL1Role.value &&
    card.value.status === "rejected" &&
    card.value.l1_owner_id === user.value.id,
);
const showCard = computed(() => !!user.value && !!card.value && !errorStatus.value);

function hasRole(roleId: number): boolean {
  return user.value?.roles.some((role) => role.id === roleId) ?? false;
}

function currentRoute(): string {
  return `${location.pathname}${location.search}${location.hash}`;
}

function rememberCardRoute(): void {
  if (cardId.value) sessionStorage.setItem(RETURN_TO_KEY, currentRoute());
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  let payload: unknown = null;
  if (response.status !== 204) {
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    throw Object.assign(new Error(), { status: response.status, detail: payload }) as ApiError;
  }
  return payload as T;
}

function errorDetail(error: unknown): unknown {
  return (error as ApiError).detail;
}

function validationMessage(detail: unknown): string {
  if (!Array.isArray(detail)) return "Проверьте введённые данные.";
  const messages = detail
    .map((item) => (typeof item === "object" && item !== null ? (item as { msg?: unknown }).msg : null))
    .filter((message): message is string => typeof message === "string");
  return messages.length ? `Проверьте данные: ${messages.join("; ")}` : "Проверьте введённые данные.";
}

const knownErrors: Record<string, string> = {
  assigned_l1_required: "Действие доступно только назначенному специалисту L1.",
  assigned_l2_required: "Действие доступно только назначенному инженеру L2.",
  l2_engineer_required: "У карточки нет назначенного инженера L2.",
  rejection_reason_required: "Укажите причину отказа.",
  status_transition_not_allowed: "Действие недоступно для текущего статуса карточки.",
};

function readableError(error: unknown): string {
  const status = (error as ApiError).status;
  const detail = errorDetail(error);
  const detailValue = detail && typeof detail === "object" && "detail" in detail ? (detail as { detail?: unknown }).detail : detail;
  if (status === 403) return "Недостаточно прав для этого действия.";
  if (status === 404) return "Карточка не найдена.";
  if (status === 401) return "Сессия завершилась. Войдите снова.";
  if (status === 409) {
    return typeof detailValue === "string" ? knownErrors[detailValue] ?? "Карточка уже изменилась. Обновите её и повторите действие." : "Карточка уже изменилась. Обновите её и повторите действие.";
  }
  if (status === 422) return validationMessage(detailValue);
  return "Не удалось выполнить действие. Попробуйте ещё раз.";
}

function handleUnauthorized(): void {
  rememberCardRoute();
  user.value = null;
  card.value = null;
  history.value = [];
}

function applyCard(value: Card): void {
  card.value = value;
  rescheduleStart.value = toLocalInput(value.planned_start_at);
  rescheduleDuration.value = value.planned_duration_minutes;
  rescheduleDescription.value = value.description ?? "";
}

async function loadHistory(): Promise<void> {
  if (!cardId.value) return;
  try {
    historyError.value = "";
    history.value = await api<HistoryEntry[]>(`/api/v1/cards/${encodeURIComponent(cardId.value)}/history`);
  } catch (error) {
    if ((error as ApiError).status === 401) {
      handleUnauthorized();
      return;
    }
    historyError.value = "Историю пока не удалось загрузить.";
  }
}

async function load(): Promise<void> {
  busy.value = true;
  errorStatus.value = null;
  actionError.value = "";
  rememberCardRoute();
  try {
    user.value = await api<User>("/api/v1/auth/me");
    if (managerPath) {
      await loadManager();
    } else if (cardId.value) {
      applyCard(await api<Card>(`/api/v1/cards/${encodeURIComponent(cardId.value)}`));
      await loadHistory();
    }
  } catch (error) {
    const status = (error as ApiError).status ?? 500;
    if (status === 401) handleUnauthorized();
    else errorStatus.value = status;
  } finally {
    busy.value = false;
  }
}

async function loadManager(): Promise<void> {
  managerLoading.value = true;
  managerError.value = "";
  const period = managerPeriod(managerFrom.value, managerTo.value);
  if (typeof period === "string") {
    managerError.value = period;
    managerLoading.value = false;
    return;
  }
  const query = new URLSearchParams();
  if (managerStatus.value) query.set("status", managerStatus.value);
  if (period.periodFrom) query.set("period_from", period.periodFrom);
  if (period.periodTo) query.set("period_to", period.periodTo);
  try {
    manager.value = await api<ManagerData>(`/api/v1/manager/cards?${query}`);
  } catch (error) {
    managerError.value = (error as ApiError).status === 403 ? "Доступ к панели руководителя запрещён (403)." : (error as ApiError).status === 401 ? "Сессия завершилась. Войдите снова." : "Не удалось загрузить панель. Повторите попытку.";
    if ((error as ApiError).status === 401) handleUnauthorized();
  } finally {
    managerLoading.value = false;
  }
}

const calendarStart = computed(() => {
  const value = managerFrom.value ? new Date(managerFrom.value) : new Date();
  value.setSeconds(0, 0);
  if (calendarMode.value === "week") {
    const day = value.getDay() || 7;
    value.setDate(value.getDate() - day + 1);
  }
  value.setHours(0, 0, 0, 0);
  return value;
});
const calendarDays = computed(() => Array.from({ length: calendarMode.value === "day" ? 1 : 7 }, (_, index) => {
  const value = new Date(calendarStart.value);
  value.setDate(value.getDate() + index);
  return value;
}));
const calendarHours = Array.from({ length: 25 }, (_, index) => index);
function calendarEventStyle(item: ManagerCard, day: Date): Record<string, string> {
  const start = new Date(item.planned_start_at);
  const end = new Date(item.planned_end_at);
  const dayStart = new Date(day);
  const dayEnd = new Date(day); dayEnd.setDate(dayEnd.getDate() + 1);
  const visibleStart = Math.max(start.getTime(), dayStart.getTime());
  const visibleEnd = Math.min(end.getTime(), dayEnd.getTime());
  const top = ((visibleStart - dayStart.getTime()) / 60000) / 15 * 20;
  const height = Math.max(24, ((visibleEnd - visibleStart) / 60000) / 15 * 20);
  return { top: `${top}px`, height: `${height}px` };
}
function calendarItems(day: Date): ManagerCard[] {
  const dayEnd = new Date(day); dayEnd.setDate(dayEnd.getDate() + 1);
  return manager.value?.items.filter((item) => new Date(item.planned_start_at) < dayEnd && new Date(item.planned_end_at) > day) ?? [];
}

async function login(): Promise<void> {
  loginBusy.value = true;
  loginError.value = "";
  rememberCardRoute();
  try {
    await api("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username: username.value, password: password.value }),
    });
    password.value = "";
    const returnTo = sessionStorage.getItem(RETURN_TO_KEY) || currentRoute();
    sessionStorage.removeItem(RETURN_TO_KEY);
    window.location.assign(returnTo);
  } catch (error) {
    loginError.value = (error as ApiError).status === 401 ? "Неверный логин или пароль." : readableError(error);
  } finally {
    loginBusy.value = false;
  }
}

async function logout(): Promise<void> {
  try {
    await api("/api/v1/auth/logout", { method: "POST" });
  } finally {
    user.value = null;
    card.value = null;
    history.value = [];
  }
}

async function runAction(name: string, path: string, init?: RequestInit): Promise<void> {
  actionBusy.value = name;
  actionError.value = "";
  try {
    await api<Card>(path, init);
    await load();
  } catch (error) {
    if ((error as ApiError).status === 401) handleUnauthorized();
    else actionError.value = readableError(error);
  } finally {
    actionBusy.value = "";
  }
}

function confirmCard(): Promise<void> {
  return runAction("confirm", `/api/v1/cards/${encodeURIComponent(cardId.value ?? "")}/confirm`, { method: "POST", body: "{}" });
}

function rejectCard(): Promise<void> {
  return runAction("reject", `/api/v1/cards/${encodeURIComponent(cardId.value ?? "")}/reject`, { method: "POST", body: JSON.stringify({ rejection_reason: rejectionReason.value }) });
}

function markClientInformed(): Promise<void> {
  return runAction("informed", `/api/v1/cards/${encodeURIComponent(cardId.value ?? "")}/l1/client-informed`, { method: "POST" });
}

function rescheduleCard(): Promise<void> {
  return runAction("reschedule", `/api/v1/cards/${encodeURIComponent(cardId.value ?? "")}/l1/reschedule`, {
    method: "POST",
    body: JSON.stringify({ planned_start_at: new Date(rescheduleStart.value).toISOString(), planned_duration_minutes: rescheduleDuration.value, description: rescheduleDescription.value || null }),
  });
}

function formatDateTime(value: string, timeZone = browserTimeZone): string {
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short", timeZone }).format(new Date(value));
}
function formatTime(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: browserTimeZone }).format(new Date(value));
}

function toLocalInput(value: string): string {
  const date = new Date(value);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatDuration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours ? `${hours} ч ${rest ? `${rest} мин` : ""}`.trim() : `${rest} мин`;
}

function personName(name: string | null, id: number | null): string {
  return name || (id === null ? "Не назначен" : "Назначен");
}

onMounted(load);
</script>

<template>
  <main class="shell">
    <section class="card" :class="{ 'manager-card': managerPath }" aria-live="polite">
      <p v-if="busy">Проверяем сессию…</p>

      <template v-else-if="!user">
        <p class="eyebrow">RDM</p>
        <h1>Вход</h1>
        <p class="muted">Войдите, чтобы открыть внутреннюю карточку.</p>
        <form class="form" @submit.prevent="login">
          <label>Логин<input v-model="username" autocomplete="username" required /></label>
          <label>Пароль<input v-model="password" type="password" autocomplete="current-password" required /></label>
          <p v-if="loginError" class="error" role="alert">{{ loginError }}</p>
          <button :disabled="loginBusy">{{ loginBusy ? "Входим…" : "Войти" }}</button>
        </form>
      </template>

      <template v-else-if="errorStatus">
        <p class="eyebrow">RDM</p>
        <h1>Не удалось открыть карточку</h1>
        <p v-if="errorStatus === 403" class="error">Доступ к карточке запрещён (403).</p>
        <p v-else-if="errorStatus === 404" class="error">Карточка не найдена (404).</p>
        <p v-else class="error">Сервис временно недоступен ({{ errorStatus }}).</p>
        <button @click="load">Повторить</button>
      </template>

      <template v-else-if="showCard && card">
        <header class="top">
          <div>
            <p class="eyebrow">Внутренняя карточка</p>
            <h1>{{ card.number }}</h1>
          </div>
          <button class="secondary" @click="logout">Выйти</button>
          <a v-if="hasRole(3)" class="button-link" href="/manager">Панель руководителя</a>
        </header>

        <div class="status-line">
          <span class="status" :class="`status-${card.status}`">{{ card.status_label }}</span>
          <span v-if="card.overdue_flag" class="flag flag-danger">Просрочено</span>
          <span v-if="card.urgency_code > 0" class="flag flag-danger">Срочно</span>
          <span v-if="card.out_of_hours_flag" class="flag">Вне рабочего времени</span>
          <span v-if="card.retroactive_flag" class="flag">Ретроспективная</span>
        </div>

        <p v-if="actionError" class="error action-error" role="alert">{{ actionError }}</p>

        <div class="grid">
          <section class="panel">
            <h2>Планирование</h2>
            <dl>
              <dt>Начало</dt><dd>{{ formatDateTime(card.planned_start_at) }}</dd>
              <dt>Окончание</dt><dd>{{ formatDateTime(card.planned_end_at) }}</dd>
              <dt>Длительность</dt><dd>{{ formatDuration(card.planned_duration_minutes) }}</dd>
              <dt>Часовой пояс отображения</dt><dd>{{ browserTimeZone }}</dd>
              <dt>То же время по Москве</dt><dd>{{ formatDateTime(card.planned_start_at, "Europe/Moscow") }} — {{ formatDateTime(card.planned_end_at, "Europe/Moscow") }}</dd>
            </dl>
          </section>

          <section class="panel">
            <h2>Ответственные</h2>
            <dl>
              <dt>L2</dt><dd>{{ personName(card.l2_engineer_name, card.l2_engineer_id) }}</dd>
              <dt>L1</dt><dd>{{ personName(card.l1_owner_name, card.l1_owner_id) }}</dd>
              <dt>Тикет Omnidesk</dt><dd>{{ card.omnidesk_ticket_number }}</dd>
            </dl>
          </section>
        </div>

        <section class="panel">
          <h2>Описание</h2>
          <p class="preserve">{{ card.description || "Описание не указано." }}</p>
        </section>

        <section v-if="card.actual_start_at || card.actual_end_at || card.engineer_report" class="panel">
          <h2>Выполнение</h2>
          <dl>
            <template v-if="card.actual_start_at"><dt>Фактическое начало</dt><dd>{{ formatDateTime(card.actual_start_at) }}</dd></template>
            <template v-if="card.actual_end_at"><dt>Фактическое окончание</dt><dd>{{ formatDateTime(card.actual_end_at) }}</dd></template>
          </dl>
          <p v-if="card.engineer_report" class="preserve">{{ card.engineer_report }}</p>
        </section>

        <section v-if="canDecideAsL2" class="panel actions">
          <h2>Решение L2</h2>
          <div class="action-row">
            <button :disabled="!!actionBusy" @click="confirmCard">{{ actionBusy === "confirm" ? "Сохраняем…" : "Подтвердить назначение" }}</button>
            <form class="inline-form" @submit.prevent="rejectCard">
              <input v-model="rejectionReason" placeholder="Причина отказа" required />
              <button class="danger" :disabled="!!actionBusy">{{ actionBusy === "reject" ? "Сохраняем…" : "Отклонить" }}</button>
            </form>
          </div>
        </section>
        <p v-else-if="hasL2Role && card.status === 'assigned'" class="hint">Решение доступно только назначенному инженеру L2.</p>

        <section v-if="canFollowUpAsL1" class="panel actions">
          <h2>Сопровождение L1</h2>
          <button v-if="!card.client_informed" :disabled="!!actionBusy" @click="markClientInformed">{{ actionBusy === "informed" ? "Сохраняем…" : "Клиент проинформирован" }}</button>
          <p v-else class="success">Клиент отмечен как проинформированный.</p>
          <form class="form reschedule-form" @submit.prevent="rescheduleCard">
            <h3>Перенос времени и описание</h3>
            <label>Новое начало<input v-model="rescheduleStart" type="datetime-local" required /></label>
            <label>Длительность, минут<input v-model.number="rescheduleDuration" type="number" min="30" max="720" required /></label>
            <label>Описание<textarea v-model="rescheduleDescription" rows="4"></textarea></label>
            <button :disabled="!!actionBusy">{{ actionBusy === "reschedule" ? "Сохраняем…" : "Сохранить изменения" }}</button>
          </form>
        </section>
        <p v-else-if="hasL1Role && card.status === 'rejected'" class="hint">Сопровождение доступно только назначенному специалисту L1.</p>

        <section class="panel history">
          <div class="section-heading"><h2>История</h2><span v-if="history.length" class="muted">{{ history.length }}</span></div>
          <p v-if="historyError" class="muted">{{ historyError }}</p>
          <p v-else-if="!history.length" class="muted">Изменений пока нет.</p>
          <ol v-else>
            <li v-for="(entry, index) in history" :key="`${entry.created_at}-${index}`">
              <div><strong>{{ entry.event_label }}</strong><span class="muted">{{ entry.actor_label }}</span></div>
              <time :datetime="entry.created_at">{{ formatDateTime(entry.created_at) }}</time>
            </li>
          </ol>
        </section>

        <footer class="footer muted">Вы вошли как {{ user.full_name || user.username }}.</footer>
      </template>

      <template v-else-if="managerPath && manager">
        <header class="top"><div><p class="eyebrow">RDM</p><h1>Панель руководителя</h1><p class="muted">Часовой пояс: {{ browserTimeZone }}</p></div><button class="secondary" @click="logout">Выйти</button></header>
        <div class="manager-stats"><div class="panel"><strong>{{ manager.summary.assigned }}</strong><span>Назначено</span></div><div class="panel"><strong>{{ manager.summary.confirmed }}</strong><span>Подтверждено</span></div><div class="panel"><strong>{{ manager.summary.rejected }}</strong><span>Отклонено</span></div><div class="panel"><strong>{{ manager.summary.overdue }}</strong><span>Просрочено</span></div></div>
        <form class="manager-filters panel" @submit.prevent="loadManager"><label>Статус<select v-model="managerStatus"><option value="">Все</option><option value="assigned">Назначено</option><option value="confirmed">Подтверждено</option><option value="rejected">Отклонено</option></select></label><label>Дата с<input v-model="managerFrom" type="date" /></label><label>Дата по<input v-model="managerTo" type="date" /></label><button>Применить</button></form>
        <p v-if="managerError" class="error" role="alert">{{ managerError }} <button class="secondary" @click="loadManager">Повторить</button></p>
        <p v-if="managerLoading" class="hint" role="status">Загрузка календаря…</p>
        <p v-else-if="manager.items.length === manager.limit" class="warning" role="status">Показаны первые {{ manager.limit }} карточек. Данные периода могут быть неполными — сузьте период или фильтр.</p>
        <div class="manager-toggle" role="group" aria-label="Режим отображения"><button type="button" :class="{ selected: managerView === 'list' }" @click="managerView = 'list'">Список</button><button type="button" :class="{ selected: managerView === 'calendar' }" @click="managerView = 'calendar'">Календарь</button><template v-if="managerView === 'calendar'"><button type="button" :class="{ selected: calendarMode === 'day' }" @click="calendarMode = 'day'">День</button><button type="button" :class="{ selected: calendarMode === 'week' }" @click="calendarMode = 'week'">Неделя</button></template></div>
        <p v-if="!managerLoading && !manager.items.length" class="hint">Карточки не найдены за выбранный период.</p>
        <div v-else-if="managerView === 'list'" class="manager-list"><a v-for="item in manager.items" :key="item.public_id" class="manager-row panel" :href="`/cards/${item.public_id}`"><div><strong>{{ item.number }}</strong><span class="muted">Тикет {{ item.omnidesk_ticket_number }}</span></div><span class="status" :class="`status-${item.status}`">{{ item.status_label }}</span><span>{{ formatDateTime(item.planned_start_at) }} · {{ formatDuration(item.planned_duration_minutes) }}</span><span>L2: {{ item.l2_engineer_name || "Не назначен" }}</span><span v-if="item.urgent || item.overdue" class="muted">{{ item.urgent ? "Срочно " : "" }}{{ item.overdue ? "Просрочено" : "" }}</span></a></div>
        <div v-else class="calendar" :style="{ '--calendar-days': String(calendarDays.length) }" :data-calendar-days="calendarDays.length"><div class="calendar-head"><span>Время</span><strong v-for="day in calendarDays" :key="day.toISOString()">{{ new Intl.DateTimeFormat("ru-RU", { weekday: "short", day: "numeric", month: "short" }).format(day) }}</strong></div><div class="calendar-body"><div class="calendar-times"><span v-for="hour in calendarHours" :key="hour">{{ String(hour).padStart(2, "0") }}:00</span></div><div v-for="day in calendarDays" :key="`col-${day.toISOString()}`" class="calendar-column"><span v-for="hour in calendarHours" :key="hour" class="calendar-line" :style="{ top: `${hour * 80}px` }"></span><a v-for="item in calendarItems(day)" :key="item.public_id" class="calendar-event" :class="[`status-${item.status}`, { urgent: item.urgent, overdue: item.overdue }]" :style="calendarEventStyle(item, day)" :href="`/cards/${item.public_id}`"><strong>{{ item.number }}</strong><span>{{ formatTime(item.planned_start_at) }}–{{ formatTime(item.planned_end_at) }} · L2: {{ item.l2_engineer_name || "Не назначен" }}</span><small>{{ item.status_label }}{{ item.urgent ? " · Срочно" : "" }}{{ item.overdue ? " · Просрочено" : "" }}</small></a></div></div></div>
      </template>

      <template v-else>
        <h1>RDM</h1>
        <p>Карточка не выбрана.</p>
        <button class="secondary" @click="logout">Выйти</button>
      </template>
    </section>
  </main>
</template>
