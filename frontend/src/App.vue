<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { getDayBoundsInTz, hasDstTransitionInRange } from "./calendar-timezone";
import { convertWallTimeToISO } from "./frame/timezone";
import { AdminWorkspace, ReportsWorkspace } from "./admin";
import SlotPicker from "./SlotPicker.vue";
import AppSidebar from "./AppSidebar.vue";
import { ScheduleEditor } from "./admin";

type Role = { id: number; name: string };
type User = { id: number; username: string; full_name: string; roles: Role[]; timezone?: string };
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
type NotificationEntry = { event: string; channel: string; status: string; created_at: string; sent_at: string | null };
type MineResponse = { items: Card[]; limit: number };
type ResultOption = { code: number; name: string };
type ApiError = Error & { status: number; detail?: unknown };
type ManagerCard = { public_id: string; number: string; omnidesk_ticket_number: string; status: string; status_label: string; planned_start_at: string; planned_end_at: string; planned_duration_minutes: number; l1_owner_name: string | null; l2_engineer_name: string | null; urgent: boolean; overdue: boolean; out_of_hours: boolean; first_unsuccessful_cycle: boolean; repeated_unsuccessful_cycle: boolean };
type ManagerData = { summary: { assigned: number; confirmed: number; rejected: number; overdue: number; urgent: number; urgent_collision: number }; items: ManagerCard[]; limit: number };
type TicketPreflight = { case_number: string; status: string; client_display_name: string | null; can_create: boolean };
type L2Option = { user_id: number; display_name: string; available: boolean; reason_code: string | null };
type CreateWindowBounds = { min: Date; max: Date };
type CreateValidationSuccess = { ok: true; start: Date; duration: number };
type CreateValidation = CreateValidationSuccess | { ok: false; error: string };

const RETURN_TO_KEY = "rdm.return_to";
const user = ref<User | null>(null);
const profileTimeZone = computed(() => user.value?.timezone || "Asia/Yekaterinburg");
const editTimezone = ref("Asia/Yekaterinburg");
const tzBusy = ref(false);
const tzError = ref("");
const tzSuccess = ref("");
const standardTimezones = [
  "Asia/Yekaterinburg",
  "Europe/Moscow",
  "Europe/Kaliningrad",
  "Europe/Samara",
  "Asia/Omsk",
  "Asia/Novosibirsk",
  "Asia/Krasnoyarsk",
  "Asia/Irkutsk",
  "Asia/Yakutsk",
  "Asia/Vladivostok",
  "Asia/Magadan",
  "Asia/Kamchatka",
  "UTC",
];

function getTzOffsetMs(date: Date, timeZone: string): number {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
  const parts = formatter.formatToParts(date);
  const map: Record<string, string> = {};
  for (const p of parts) map[p.type] = p.value;
  const hour = Number(map.hour) % 24;
  const asUtc = Date.UTC(
    Number(map.year),
    Number(map.month) - 1,
    Number(map.day),
    hour,
    Number(map.minute),
    Number(map.second)
  );
  return asUtc - date.getTime();
}

function dateInTz(dateValue: string, dayOffset = 0, timeZone = profileTimeZone.value): Date {
  const [year, month, day] = dateValue.split("-").map(Number);
  const baseUtc = new Date(Date.UTC(year, month - 1, day + dayOffset, 0, 0, 0));
  const offset1 = getTzOffsetMs(baseUtc, timeZone);
  const adjusted = new Date(baseUtc.getTime() - offset1);
  const offset2 = getTzOffsetMs(adjusted, timeZone);
  return new Date(baseUtc.getTime() - offset2);
}

function profileDateTimeToIso(dateTimeValue: string, timeZone = profileTimeZone.value): string {
  if (!dateTimeValue) return "";
  const [datePart, timePart] = dateTimeValue.split("T");
  return convertWallTimeToISO(datePart, timePart, timeZone);
}

function toProfileInput(value: string, timeZone = profileTimeZone.value): string {
  const date = new Date(value);
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const parts = formatter.formatToParts(date);
  const map: Record<string, string> = {};
  for (const p of parts) map[p.type] = p.value;
  const hour = String(Number(map.hour) % 24).padStart(2, "0");
  return `${map.year}-${map.month}-${map.day}T${hour}:${map.minute}`;
}

const card = ref<Card | null>(null);
const history = ref<HistoryEntry[]>([]);
const notifications = ref<NotificationEntry[]>([]);
const notificationError = ref("");
const mine = ref<Card[]>([]);
const mineError = ref("");
const mineRole = ref<"l1" | "l2">("l1");
const roleCreate = ref({ caseNumber: "", start: "", duration: 60, description: "", scenario: "normal", urgentReason: "", resultCode: "", report: "" });
const roleCreateBusy = ref(false);
const roleCreateError = ref("");
const results = ref<ResultOption[]>([]);
const completion = ref({ resultCode: "", report: "", duration: "" });
const cancellationReason = ref("");
const rescheduleReason = ref("");
const assignL2Id = ref("");
const assignmentReason = ref("");
const assignmentOptions = ref<L2Option[]>([]);
const reminderInterval = ref(10);
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
const currentPath = location.pathname;
const showSidebar = computed(() => !!user.value && !busy.value);
const workplacePath = location.pathname === "/work" || location.pathname === "/";
const managerPath = location.pathname === "/manager";
const managerNewPath = location.pathname === "/manager/cards/new";
const adminPath = location.pathname === "/admin";
const reportsPath = location.pathname === "/reports";
const profilePath = location.pathname === "/profile";
const schedulesPath = location.pathname === "/schedules";
const manager = ref<ManagerData | null>(null);
const managerStatus = ref("");
const managerFrom = ref("");
const managerTo = ref("");
const managerError = ref("");
const managerView = ref<"list" | "calendar">("list");
const calendarMode = ref<"day" | "week">("week");
const managerLoading = ref(false);
const attentionItems = computed(() => manager.value?.items.filter((item) => item.first_unsuccessful_cycle || item.repeated_unsuccessful_cycle || item.overdue || item.urgent) ?? []);
const create = ref({ caseNumber: "", start: "", duration: 60, description: "", assignment: "auto", l2UserId: "" });
const ticketPreflight = ref<TicketPreflight | null>(null);
const l2Options = ref<L2Option[]>([]);
const preflightLoading = ref(false);
const createLoading = ref(false);
const createBusy = ref(false);
const createError = ref("");
const createNotice = ref("");
const ticketRequest = ref(0);
const l2Request = ref(0);
const createNow = ref(new Date());
let createTimer: ReturnType<typeof setInterval> | undefined;

type ManagerPeriod = { periodFrom: string | null; periodTo: string | null };

function managerPeriod(from: string, to: string): ManagerPeriod | string {
  if (from && to && dateInTz(to, 0) < dateInTz(from, 0)) return "Дата окончания не может быть раньше даты начала.";
  return { periodFrom: from ? dateInTz(from, 0).toISOString() : null, periodTo: to ? dateInTz(to, 1).toISOString() : null };
}

const hasL1Role = computed(() => hasRole(1));
const hasL2Role = computed(() => hasRole(2));
const isAssignedL2 = computed(() => !!user.value && hasL2Role.value && card.value?.l2_engineer_id === user.value.id);
const canDecide = computed(
  () =>
    !!user.value &&
    !!card.value &&
    (isAssignedL2.value || hasRole(3)) &&
    card.value.status === "assigned" &&
    card.value.l2_engineer_id !== null,
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
const canExecute = computed(() => !!card.value && (isAssignedL2.value || hasRole(3)) && ["assigned", "confirmed", "in_progress"].includes(card.value.status));
const canEndPendingResult = computed(() => !!card.value && (isAssignedL2.value || hasRole(3)) && card.value.status === "in_progress");
const canComplete = computed(
  () =>
    !!card.value &&
    (card.value.status === "completed_pending_result"
      ? isAssignedL2.value
      : (isAssignedL2.value || hasRole(3)) && card.value.status === "in_progress")
);
const canCancel = computed(() => !!card.value && (hasRole(3) ? ["assigned", "confirmed", "rejected", "in_progress"].includes(card.value.status) : (hasL1Role.value || hasL2Role.value) && ["assigned", "confirmed", "rejected"].includes(card.value.status)));
const canReschedule = computed(() => !!card.value && ["assigned", "confirmed", "rejected"].includes(card.value.status) && (hasRole(3) || hasL1Role.value || hasL2Role.value));
const canAssign = computed(() => !!card.value && hasRole(3) && ["created", "assigned", "confirmed", "rejected"].includes(card.value.status));
const canSelfAssign = computed(
  () =>
    !!card.value &&
    !hasRole(3) &&
    hasL2Role.value &&
    card.value.l2_engineer_id !== user.value?.id &&
    ["created", "assigned", "confirmed", "rejected"].includes(card.value.status)
);

function hasRole(roleId: number): boolean {
  return user.value?.roles.some((role) => role.id === roleId) ?? false;
}

function currentRoute(): string {
  return `${location.pathname}${location.search}${location.hash}`;
}

function forgetReturnRoute(): void {
  try { sessionStorage.removeItem(RETURN_TO_KEY); } catch { /* Storage may be unavailable. */ }
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
  action_not_allowed_for_status: "Действие недоступно для текущего статуса карточки.",
  cancellation_reason_required: "Укажите причину отмены.",
  reschedule_reason_required: "Укажите причину переноса.",
  assignment_reason_required: "Укажите причину назначения.",
  only_self_assignment_allowed: "Инженеру L2 разрешено назначать карточку только на себя.",
  only_assigned_l2_may_submit_missing_result: "Отправить результат может только назначенный инженер L2.",
  urgent_reason_required: "Укажите причину срочности.",
  retroactive_result_required: "Укажите результат подключения.",
  retroactive_engineer_report_required: "Укажите отчёт о выполненных работах.",
  urgent_planned_start_must_not_be_in_past: "Плановое начало срочного подключения не может быть в прошлом.",
  retroactive_planned_start_must_be_in_past: "Плановое начало ретроспективного подключения должно быть в прошлом.",
  planned_start_too_soon: "Начало должно быть не раньше минимального срока — за 2 часа.",
  planned_start_too_far: "Начало не может быть дальше горизонта 14 дней.",
  active_card_exists_for_ticket: "Для этого тикета уже есть активная карточка.",
  l2_assignment_conflict: "Выбранный L2 стал недоступен. Обновите варианты и выберите другого.",
  card_owner_required: "Действие доступно только владельцу карточки.",
  l1_followup_not_informed: "Сначала отметьте, что клиент проинформирован.",
  action_forbidden: "Недостаточно прав для этого действия.",
  omnidesk_ticket_not_found: "Тикет с таким номером не найден в индексе RDM. Проверьте номер или обратитесь к администратору.",
  omnidesk_ticket_ambiguous: "Номеру тикета соответствует несколько записей. Обратитесь к администратору.",
  omnidesk_ticket_id_number_mismatch: "Номер тикета не совпадает с данными Omnidesk. Обратитесь к администратору.",
  omnidesk_unavailable: "Omnidesk сейчас недоступен. Повторите попытку позже.",
  ticket_client_missing: "У тикета в Omnidesk не указан клиент, создать карточку нельзя.",
};

function readableError(error: unknown): string {
  const status = (error as ApiError).status;
  const detail = errorDetail(error);
  const detailValue = detail && typeof detail === "object" && "detail" in detail ? (detail as { detail?: unknown }).detail : detail;
  const errorKey = typeof detailValue === "string" ? detailValue : "";
  const mapped = knownErrors[errorKey];
  if (mapped) return mapped;
  if (status === 403) return "Недостаточно прав для этого действия.";
  if (status === 404) return "Карточка не найдена.";
  if (status === 401) return "Сессия завершилась. Войдите снова.";
  if (status === 409) return "Карточка уже изменилась. Обновите её и повторите действие.";
  if (status === 422) return Array.isArray(detailValue) ? validationMessage(detailValue) : "Проверьте введённые данные.";
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
  rescheduleStart.value = toProfileInput(value.planned_start_at);
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

async function loadNotifications(): Promise<void> {
  if (!cardId.value) return;
  notifications.value = [];
  notificationError.value = "";
  try {
    const data = await api<{ items: NotificationEntry[] }>(`/api/v1/cards/${encodeURIComponent(cardId.value)}/notifications`);
    notifications.value = data.items;
  } catch (error) {
    if ((error as ApiError).status === 401) {
      handleUnauthorized();
    } else if ((error as ApiError).status === 403) {
      notificationError.value = "Доступ к уведомлениям ограничен (403).";
    } else {
      notificationError.value = "Состояние уведомлений пока недоступно.";
    }
  }
}

async function loadMine(): Promise<void> {
  mineError.value = "";
  mine.value = [];
  try {
    const data = await api<MineResponse>(`/api/v1/cards/mine?role=${mineRole.value}`);
    mine.value = data.items;
  } catch (error) {
    if ((error as ApiError).status === 401) handleUnauthorized();
    else mineError.value = readableError(error);
  }
}

async function loadResults(): Promise<void> {
  try {
    const data = await api<{ items: ResultOption[] }>("/api/v1/cards/results");
    results.value = data.items;
  } catch (error) {
    if ((error as ApiError).status === 401) handleUnauthorized();
  }
}

async function loadAssignmentOptions(): Promise<void> {
  if (!card.value || !canAssign.value) return;
  assignmentOptions.value = [];
  try {
    const query = new URLSearchParams({ planned_start_at: card.value.planned_start_at, planned_duration_minutes: String(card.value.planned_duration_minutes) });
    const data = await api<{ items: L2Option[] }>(`/api/v1/manager/l2-options?${query}`);
    assignmentOptions.value = data.items;
  } catch (error) {
    actionError.value = readableError(error);
  }
}

async function loadReminderInterval(): Promise<void> {
  if (!cardId.value || !canFollowUpAsL1.value || !card.value?.client_informed) return;
  try {
    const data = await api<{ interval_minutes: number }>(`/api/v1/cards/${encodeURIComponent(cardId.value)}/l1/reminder-interval`);
    reminderInterval.value = data.interval_minutes;
  } catch (error) {
    if ((error as ApiError).status === 401) handleUnauthorized();
  }
}

async function load(): Promise<void> {
  busy.value = true;
  errorStatus.value = null;
  actionError.value = "";
  try {
    user.value = await api<User>("/api/v1/auth/me");
    forgetReturnRoute();
    editTimezone.value = user.value.timezone || "Asia/Yekaterinburg";
    if (hasL1Role.value || hasL2Role.value || hasRole(3)) await loadPlanning();
    if (workplacePath) {
      mineRole.value = hasL1Role.value ? "l1" : "l2";
      if (hasL1Role.value || hasL2Role.value) await loadMine();
      if (hasL2Role.value) await loadResults();
    } else if (managerPath) {
      if (hasRole(3)) await loadManager();
      else managerError.value = "Доступ к панели руководителя запрещён (403).";
    } else if (cardId.value) {
      applyCard(await api<Card>(`/api/v1/cards/${encodeURIComponent(cardId.value)}`));
      await loadHistory();
      await loadNotifications();
      if (canFollowUpAsL1.value) await loadReminderInterval();
      if (canAssign.value) await loadAssignmentOptions();
      if (canComplete.value) await loadResults();
    }
  } catch (error) {
    const status = (error as ApiError).status ?? 500;
    if (status === 401) handleUnauthorized();
    else {
      if (status === 403 || status === 404) forgetReturnRoute();
      errorStatus.value = status;
    }
  } finally {
    busy.value = false;
  }
}

function createErrorMessage(error: unknown): string {
  const status = (error as ApiError).status;
  const detail = errorDetail(error);
  const value = detail && typeof detail === "object" && "detail" in detail ? (detail as { detail?: unknown }).detail : detail;
  const messages: Record<string, string> = {
    planned_start_too_soon: "Начало должно быть не раньше минимального срока — за 2 часа.",
    planned_start_too_far: "Начало не может быть дальше горизонта 14 дней.",
    l2_assignment_conflict: "Выбранный L2 стал недоступен. Обновите варианты и выберите другого.",
    active_card_exists_for_ticket: "Для этого тикета уже есть активная карточка.",
    omnidesk_unavailable: "Omnidesk временно недоступен. Повторите попытку позже.",
  };
  if (status === 401) return "Сессия завершилась. Войдите снова.";
  if (status === 403) return "Создание карточек доступно только руководителю (403).";
  if (status === 409) return typeof value === "string" ? messages[value] ?? "Данные изменились. Проверьте тикет и назначение заново." : "Данные изменились. Проверьте тикет и назначение заново.";
  if (status === 422) return Array.isArray(value) ? validationMessage(value) : messages[String(value)] ?? "Проверьте данные формы.";
  if (status === 404) return "Тикет не найден или номер не совпадает.";
  return "Не удалось выполнить запрос. Повторите попытку.";
}

type PlanningWindow = { min_lead_minutes: number; horizon_days: number; default_duration_minutes: number; min_duration_minutes: number; max_duration_minutes: number };
const planning = ref<PlanningWindow>({ min_lead_minutes: 120, horizon_days: 14, default_duration_minutes: 60, min_duration_minutes: 30, max_duration_minutes: 720 });
async function loadPlanning(): Promise<void> {
  try {
    const data = await api<Partial<PlanningWindow>>("/api/v1/cards/planning-window");
    const next = { ...planning.value };
    for (const key of Object.keys(next) as Array<keyof PlanningWindow>) {
      const value = data?.[key];
      if (typeof value === "number" && Number.isFinite(value) && value > 0) next[key] = value;
    }
    planning.value = next;
  } catch { /* Defaults match the server defaults. */ }
}
function plural(count: number, one: string, few: string, many: string): string {
  const last = count % 10; const lastTwo = count % 100;
  if (last === 1 && lastTwo !== 11) return one;
  if (last >= 2 && last <= 4 && (lastTwo < 10 || lastTwo >= 20)) return few;
  return many;
}
function leadText(minutes: number): string {
  return minutes % 60 === 0 ? `${minutes / 60} ${plural(minutes / 60, "час", "часа", "часов")}` : `${minutes} ${plural(minutes, "минуту", "минуты", "минут")}`;
}
function createWindowBounds(now: Date): CreateWindowBounds {
  return { min: ceilToMinute(new Date(now.getTime() + planning.value.min_lead_minutes * 60 * 1000)), max: floorToMinute(new Date(now.getTime() + planning.value.horizon_days * 24 * 60 * 60 * 1000)) };
}
type SlotKind = "normal" | "urgent" | "retroactive";
function slotWindow(kind: SlotKind, now: Date): { min: string; max: string } {
  const horizon = floorToMinute(new Date(now.getTime() + planning.value.horizon_days * 24 * 60 * 60 * 1000));
  if (kind === "retroactive") return { min: localDateTimeInput(new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)), max: localDateTimeInput(floorToMinute(new Date(now.getTime() - 60 * 1000))) };
  if (kind === "urgent") return { min: localDateTimeInput(ceilToMinute(now)), max: localDateTimeInput(horizon) };
  const bounds = createWindowBounds(now);
  return { min: localDateTimeInput(bounds.min), max: localDateTimeInput(bounds.max) };
}
function ceilToMinute(value: Date): Date {
  const result = new Date(value);
  result.setSeconds(0, 0);
  if (value.getSeconds() !== 0 || value.getMilliseconds() !== 0) result.setMinutes(result.getMinutes() + 1);
  return result;
}
function floorToMinute(value: Date): Date {
  const result = new Date(value);
  result.setSeconds(0, 0);
  return result;
}
function localDateTimeInput(value: Date): string {
  return toProfileInput(value.toISOString());
}
function validateCreateWindow(startValue: string, durationValue: number, now: Date): CreateValidation {
  let start: Date;
  try {
    start = new Date(profileDateTimeToIso(startValue));
  } catch {
    return { ok: false, error: "Укажите корректные дату и время начала." };
  }
  if (!startValue || Number.isNaN(start.getTime())) return { ok: false, error: "Укажите корректные дату и время начала." };
  if (toProfileInput(start.toISOString()) !== startValue.slice(0, 16)) return { ok: false, error: "Указанное время не существует в часовом поясе профиля." };
  const duration = Number(durationValue);
  if (!Number.isInteger(duration) || duration < planning.value.min_duration_minutes || duration > planning.value.max_duration_minutes) return { ok: false, error: `Длительность должна быть от ${planning.value.min_duration_minutes} до ${planning.value.max_duration_minutes} минут.` };
  const bounds = createWindowBounds(now);
  if (start < bounds.min) return { ok: false, error: `Начало должно быть не раньше чем через ${leadText(planning.value.min_lead_minutes)}.` };
  if (start > bounds.max) return { ok: false, error: `Начало не может быть дальше чем через ${planning.value.horizon_days} ${plural(planning.value.horizon_days, "день", "дня", "дней")}.` };
  return { ok: true, start, duration };
}
function validateBeforeCreateHttp(): CreateValidationSuccess | null {
  createNow.value = new Date();
  const result = validateCreateWindow(create.value.start, create.value.duration, createNow.value);
  if (!result.ok) { createError.value = result.error; return null; }
  return result;
}
const createBounds = computed(() => createWindowBounds(createNow.value));
const roleKind = computed<SlotKind>(() => (mineRole.value === "l2" && roleCreate.value.scenario === "urgent" ? "urgent" : mineRole.value === "l2" && roleCreate.value.scenario === "retroactive" ? "retroactive" : "normal"));
const roleWindow = computed(() => slotWindow(roleKind.value, createNow.value));
const rescheduleWindow = computed(() => slotWindow(card.value?.urgency_code === 1 ? "urgent" : "normal", createNow.value));
const createMin = computed(() => localDateTimeInput(createBounds.value.min));
const createMax = computed(() => localDateTimeInput(createBounds.value.max));
function createStartIso(start: Date): string { return start.toISOString(); }
async function preflightTicket(): Promise<void> {
  const request = ++ticketRequest.value;
  ticketPreflight.value = null; createNotice.value = "";
  if (!create.value.caseNumber) return;
  const caseNumber = create.value.caseNumber;
  preflightLoading.value = true; createError.value = "";
  try {
    const data = await api<TicketPreflight>(`/api/v1/manager/tickets/${encodeURIComponent(caseNumber)}/preflight`);
    if (request === ticketRequest.value) ticketPreflight.value = data;
  } catch (error) {
    if (request === ticketRequest.value) { createError.value = createErrorMessage(error); if ((error as ApiError).status === 401) handleUnauthorized(); }
  } finally {
    if (request === ticketRequest.value) preflightLoading.value = false;
  }
}
async function loadL2Options(): Promise<void> {
  const request = ++l2Request.value; l2Options.value = [];
  const valid = validateBeforeCreateHttp();
  if (valid === null) return;
  createLoading.value = true;
  try { const data = await api<{ items: L2Option[] }>(`/api/v1/manager/l2-options?planned_start_at=${encodeURIComponent(createStartIso(valid.start))}&planned_duration_minutes=${valid.duration}`); if (request === l2Request.value) l2Options.value = data.items; }
  catch (error) { if (request === l2Request.value) createError.value = createErrorMessage(error); }
  finally { if (request === l2Request.value) createLoading.value = false; }
}
async function submitCreate(): Promise<void> {
  if (createBusy.value || preflightLoading.value || createLoading.value) return;
  if (!ticketPreflight.value || !ticketPreflight.value.can_create || ticketPreflight.value.case_number !== create.value.caseNumber) {
    createError.value = "Проверьте тикет перед созданием карточки.";
    return;
  }
  if (create.value.assignment === "manual" && !create.value.l2UserId) {
    createError.value = "Выберите инженера L2.";
    return;
  }
  const valid = validateBeforeCreateHttp();
  if (valid === null) return;
  createBusy.value = true; createError.value = "";
  try {
    const payload: Record<string, unknown> = { case_number: create.value.caseNumber, planned_start_at: createStartIso(valid.start), planned_duration_minutes: valid.duration, description: create.value.description || null, assignment_method: "auto" };
    if (create.value.assignment === "manual") payload.l2_user_id = Number(create.value.l2UserId);
    const created = await api<Card>("/api/v1/manager/cards", { method: "POST", body: JSON.stringify(payload) });
    window.location.assign(`/cards/${created.id}`);
  } catch (error) {
    createError.value = createErrorMessage(error);
    createBusy.value = false;
    if ((error as ApiError).status === 401) handleUnauthorized();
    else if ((error as ApiError).status === 409) await loadL2Options();
  }
}
watch(() => create.value.caseNumber, () => {
  ticketPreflight.value = null;
  ticketRequest.value++;
  preflightLoading.value = false;
  createError.value = "";
});
watch(() => [create.value.start, create.value.duration], loadL2Options);

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
  const baseDateStr = managerFrom.value || "";
  let baseDate: Date;
  if (baseDateStr) {
    baseDate = dateInTz(baseDateStr, 0);
  } else {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: profileTimeZone.value, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
    const m: Record<string, string> = {};
    for (const p of parts) m[p.type] = p.value;
    baseDate = dateInTz(`${m.year}-${m.month}-${m.day}`, 0);
  }
  if (calendarMode.value === "week") {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: profileTimeZone.value, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(baseDate);
    const m: Record<string, string> = {};
    for (const p of parts) m[p.type] = p.value;
    const d = new Date(Date.UTC(Number(m.year), Number(m.month) - 1, Number(m.day)));
    const weekday = d.getUTCDay() || 7;
    return dateInTz(`${m.year}-${m.month}-${m.day}`, -(weekday - 1));
  }
  return baseDate;
});
const calendarDays = computed(() => Array.from({ length: calendarMode.value === "day" ? 1 : 7 }, (_, index) => {
  const startDate = toProfileInput(calendarStart.value.toISOString()).slice(0, 10);
  return dateInTz(startDate, index);
}));
const calendarHours = Array.from({ length: 25 }, (_, index) => index);
const hasCalendarDstTransition = computed(() => {
  return hasDstTransitionInRange(calendarDays.value, profileTimeZone.value);
});
const calendarPeriodItems = computed(() => {
  if (!manager.value?.items.length || !calendarDays.value.length) return [];
  const firstDay = calendarDays.value[0];
  const lastDay = calendarDays.value[calendarDays.value.length - 1];
  const startBounds = getDayBoundsInTz(firstDay, profileTimeZone.value);
  const endBounds = getDayBoundsInTz(lastDay, profileTimeZone.value);
  const periodStart = startBounds.dayStart.getTime();
  const periodEnd = endBounds.dayEnd.getTime();
  return manager.value.items.filter((item) => {
    const start = new Date(item.planned_start_at).getTime();
    const end = new Date(item.planned_end_at).getTime();
    return start < periodEnd && end > periodStart;
  });
});
function calendarEventStyle(item: ManagerCard, day: Date): Record<string, string> {
  const start = new Date(item.planned_start_at).getTime();
  const end = new Date(item.planned_end_at).getTime();
  const bounds = getDayBoundsInTz(day, profileTimeZone.value);
  const dayStart = bounds.dayStart.getTime();
  const dayEnd = bounds.dayEnd.getTime();
  const visibleStart = Math.max(start, dayStart);
  const visibleEnd = Math.min(end, dayEnd);
  const top = ((visibleStart - dayStart) / 60000) / 15 * 20;
  const height = Math.max(24, ((visibleEnd - visibleStart) / 60000) / 15 * 20);
  return { top: `${top}px`, height: `${height}px` };
}
function calendarItems(day: Date): ManagerCard[] {
  const bounds = getDayBoundsInTz(day, profileTimeZone.value);
  const dayStart = bounds.dayStart.getTime();
  const dayEnd = bounds.dayEnd.getTime();
  return manager.value?.items.filter((item) => new Date(item.planned_start_at).getTime() < dayEnd && new Date(item.planned_end_at).getTime() > dayStart) ?? [];
}

async function login(): Promise<void> {
  loginBusy.value = true;
  loginError.value = "";
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
    // An explicit sign-out must not bring the next login back to the page just left.
    forgetReturnRoute();
    user.value = null;
    card.value = null;
    history.value = [];
    if (location.pathname !== "/") window.location.assign("/");
  }
}

async function saveProfileTimezone(): Promise<void> {
  if (tzBusy.value || !editTimezone.value.trim()) return;
  tzBusy.value = true;
  tzError.value = "";
  tzSuccess.value = "";
  try {
    const res = await api<{ user_id: number; timezone: string }>("/api/v1/auth/timezone", {
      method: "PUT",
      body: JSON.stringify({ timezone: editTimezone.value.trim() }),
    });
    if (user.value) {
      user.value.timezone = res.timezone;
    }
    editTimezone.value = res.timezone;
    tzSuccess.value = "Часовой пояс сохранён.";
    if (managerPath && manager.value) {
      await loadManager();
    }
  } catch (error) {
    if ((error as ApiError).status === 401) {
      handleUnauthorized();
    } else if ((error as ApiError).status === 422) {
      tzError.value = "Недопустимый часовой пояс IANA.";
    } else {
      tzError.value = readableError(error);
    }
  } finally {
    tzBusy.value = false;
  }
}

async function runAction(name: string, path: string, init?: RequestInit): Promise<void> {
  if (actionBusy.value) return;
  actionBusy.value = name;
  actionError.value = "";
  try {
    await api<Card>(path, init);
    await load();
  } catch (error) {
    if ((error as ApiError).status === 401) handleUnauthorized();
    else {
      actionError.value = readableError(error);
      if ((error as ApiError).status === 409) {
        try { applyCard(await api<Card>(`/api/v1/cards/${encodeURIComponent(cardId.value ?? "")}`)); } catch { /* Keep the conflict visible. */ }
      }
    }
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
  if (!rescheduleReason.value.trim()) {
    actionError.value = "Укажите причину переноса.";
    return Promise.resolve();
  }
  let plannedStart: string;
  try {
    plannedStart = profileDateTimeToIso(rescheduleStart.value);
  } catch {
    actionError.value = "Укажите существующее и однозначное время в часовом поясе профиля.";
    return Promise.resolve();
  }
  return runAction("reschedule", `/api/v1/cards/${encodeURIComponent(cardId.value ?? "")}/l1/reschedule`, {
    method: "POST",
    body: JSON.stringify({ planned_start_at: plannedStart, planned_duration_minutes: rescheduleDuration.value, description: rescheduleDescription.value || null, reason: rescheduleReason.value || null }),
  });
}

function startCard(): Promise<void> {
  return runAction("start", `/api/v1/cards/${encodeURIComponent(cardId.value ?? "")}/start`, { method: "POST", body: "{}" });
}

function endPendingResult(): Promise<void> {
  return runAction("end_pending_result", `/api/v1/cards/${encodeURIComponent(cardId.value ?? "")}/end-pending-result`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

function completeCard(): Promise<void> {
  if (!results.value.some((result) => result.code === Number(completion.value.resultCode))) {
    actionError.value = "Выберите действующий результат подключения.";
    return Promise.resolve();
  }
  return runAction("complete", `/api/v1/cards/${encodeURIComponent(cardId.value ?? "")}/complete`, { method: "POST", body: JSON.stringify({ result_code: Number(completion.value.resultCode), engineer_report: completion.value.report, actual_duration_minutes: completion.value.duration ? Number(completion.value.duration) : null }) });
}

function cancelCard(): Promise<void> {
  if (!cancellationReason.value.trim()) {
    actionError.value = "Укажите причину отмены.";
    return Promise.resolve();
  }
  return runAction("cancel", `/api/v1/cards/${encodeURIComponent(cardId.value ?? "")}/cancel`, { method: "POST", body: JSON.stringify({ comment: cancellationReason.value || null }) });
}

function assignCard(): Promise<void> {
  if (!assignmentOptions.value.some((option) => option.available && option.user_id === Number(assignL2Id.value))) {
    actionError.value = "Выберите доступного инженера L2.";
    return Promise.resolve();
  }
  return runAction("assign", `/api/v1/cards/${encodeURIComponent(cardId.value ?? "")}/assign`, { method: "POST", body: JSON.stringify({ l2_engineer_id: Number(assignL2Id.value), comment: assignmentReason.value || null }) });
}

function selfAssignCard(): Promise<void> {
  if (!user.value || !assignmentReason.value.trim()) {
    actionError.value = "Укажите причину назначения.";
    return Promise.resolve();
  }
  return runAction("assign", `/api/v1/cards/${encodeURIComponent(cardId.value ?? "")}/assign`, { method: "POST", body: JSON.stringify({ l2_engineer_id: user.value.id, comment: assignmentReason.value }) });
}

async function setReminderInterval(): Promise<void> {
  if (!cardId.value || actionBusy.value) return;
  actionBusy.value = "reminder";
  actionError.value = "";
  try {
    const data = await api<{ interval_minutes: number }>(`/api/v1/cards/${encodeURIComponent(cardId.value)}/l1/reminder-interval`, { method: "POST", body: JSON.stringify({ interval_minutes: reminderInterval.value }) });
    reminderInterval.value = data.interval_minutes;
  } catch (error) {
    if ((error as ApiError).status === 401) handleUnauthorized();
    else actionError.value = readableError(error);
  } finally { actionBusy.value = ""; }
}

async function submitRoleCreate(): Promise<void> {
  if (!user.value || roleCreateBusy.value) return;
  let plannedStart: string;
  try {
    plannedStart = profileDateTimeToIso(roleCreate.value.start);
  } catch {
    roleCreateError.value = "Укажите существующее и однозначное время в часовом поясе профиля.";
    return;
  }
  roleCreateBusy.value = true;
  roleCreateError.value = "";
  const scenario = mineRole.value === "l1" ? "l1" : roleCreate.value.scenario === "urgent" ? "l2/urgent" : roleCreate.value.scenario === "retroactive" ? "l2/retroactive" : "l2";
  const payload: Record<string, unknown> = { case_number: roleCreate.value.caseNumber, planned_start_at: plannedStart, planned_duration_minutes: roleCreate.value.duration, description: roleCreate.value.description || null };
  if (scenario === "l2/urgent") payload.urgent_reason = roleCreate.value.urgentReason;
  if (scenario === "l2/retroactive") {
    if (roleCreate.value.resultCode) payload.result_code = Number(roleCreate.value.resultCode);
    if (roleCreate.value.report) payload.engineer_report = roleCreate.value.report;
  }
  try {
    const created = await api<Card>(`/api/v1/cards/${scenario}`, { method: "POST", body: JSON.stringify(payload) });
    window.location.assign(`/cards/${created.id}`);
  } catch (error) {
    if ((error as ApiError).status === 401) handleUnauthorized();
    else roleCreateError.value = readableError(error);
  } finally { roleCreateBusy.value = false; }
}

function formatDateTime(value: string, timeZone = profileTimeZone.value): string {
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short", timeZone }).format(new Date(value));
}
function formatTime(value: string, timeZone = profileTimeZone.value): string {
  return new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone }).format(new Date(value));
}

function formatDuration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours ? `${hours} ч ${rest ? `${rest} мин` : ""}`.trim() : `${rest} мин`;
}

function personName(name: string | null, id: number | null): string {
  return name || (id === null ? "Не назначен" : "Назначен");
}

function refreshCreateNow(): void { createNow.value = new Date(); }
onMounted(() => {
  refreshCreateNow();
  createTimer = setInterval(refreshCreateNow, 60_000);
  load();
});
onBeforeUnmount(() => { if (createTimer) clearInterval(createTimer); });
</script>

<template>
  <main class="shell" :class="{ 'with-sidebar': showSidebar }">
    <AppSidebar v-if="showSidebar && user" :roles="user.roles.map((role) => role.id)" :full-name="user.full_name" :path="currentPath" @logout="logout" />
    <section class="card" :class="{ 'manager-card': managerPath && !!user }" aria-live="polite">
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
        <div class="top-actions"><button @click="load">Повторить</button><a class="button-link" href="/" @click="forgetReturnRoute">На главную</a></div>
      </template>

      <template v-else-if="profilePath">
        <header class="top"><div><p class="eyebrow">RDM</p><h1>Профиль</h1><p class="muted">{{ user?.full_name }} · {{ user?.username }}</p></div></header>
        <section class="panel profile-tz-panel">
          <h2>Часовой пояс</h2>
          <p class="muted">По нему отображается время карточек и календарей. Сохранённые моменты времени при смене пояса не меняются.</p>
          <form class="inline-form" @submit.prevent="saveProfileTimezone">
            <input v-model="editTimezone" list="staff-timezones" aria-label="Часовой пояс профиля" />
            <datalist id="staff-timezones"><option v-for="tz in standardTimezones" :key="tz" :value="tz" /></datalist>
            <button type="submit" :disabled="tzBusy">{{ tzBusy ? "Сохраняем…" : "Сохранить часовой пояс" }}</button>
          </form>
          <p v-if="tzError" class="error profile-tz-error" role="alert">{{ tzError }}</p>
          <p v-if="tzSuccess" class="success profile-tz-success" role="status">{{ tzSuccess }}</p>
        </section>
      </template>

      <template v-else-if="schedulesPath">
        <header class="top"><div><p class="eyebrow">RDM</p><h1>Графики работы</h1></div></header>
        <ScheduleEditor v-if="hasRole(3) || hasRole(4)" />
        <p v-else class="error" role="alert">Доступ к графикам работы запрещён (403).</p>
      </template>

      <template v-else-if="adminPath || reportsPath">
        <header class="top"><div><p class="eyebrow">RDM</p><h1>{{ adminPath ? "Администрирование" : "Отчёты" }}</h1><p class="muted">Часовой пояс: {{ profileTimeZone }}</p></div></header>
        <AdminWorkspace v-if="adminPath" :current-user="user" :timezone="profileTimeZone" />
        <ReportsWorkspace v-else :current-user="user" :timezone="profileTimeZone" />
      </template>

      <template v-else-if="workplacePath">
        <header class="top"><div><p class="eyebrow">RDM</p><h1>Мои карточки</h1></div></header>
        <div v-if="hasL1Role && hasL2Role" class="manager-toggle" role="group" aria-label="Рабочая роль"><button type="button" :class="{ selected: mineRole === 'l1' }" @click="mineRole = 'l1'; loadMine()">L1</button><button type="button" :class="{ selected: mineRole === 'l2' }" @click="mineRole = 'l2'; loadMine()">L2</button></div>
        <p v-if="mineError" class="error" role="alert">{{ mineError }}</p>
        <p v-else-if="!mine.length" class="hint">Назначенных карточек пока нет.</p>
        <div v-else class="work-list"><a v-for="item in mine" :key="item.id" class="panel work-row" :href="`/cards/${item.id}`"><strong>{{ item.number }}</strong><span>{{ item.status_label }}<template v-if="item.overdue_flag"> · Просрочено</template></span><span>{{ formatDateTime(item.planned_start_at) }}</span></a></div>
        <section v-if="hasL1Role || hasL2Role" class="panel"><h2>Создать карточку {{ mineRole.toUpperCase() }}</h2>
          <form class="form role-create-form" @submit.prevent="submitRoleCreate">
            <label>Номер тикета<input v-model.trim="roleCreate.caseNumber" pattern="[0-9]{3}-[0-9]{6}" required /></label>
            <label>Начало<SlotPicker v-model="roleCreate.start" :min="roleWindow.min" :max="roleWindow.max" required data-test="role-slot" /></label>
            <label>Длительность, минут<input v-model.number="roleCreate.duration" type="number" :min="planning.min_duration_minutes" :max="planning.max_duration_minutes" required /></label>
            <label>Описание<textarea v-model="roleCreate.description" rows="3"></textarea></label>
            <template v-if="mineRole === 'l2'"><label>Сценарий<select v-model="roleCreate.scenario"><option value="normal">Обычное для себя</option><option value="urgent">Срочное для себя</option><option value="retroactive">Ретроспективное для себя</option></select></label>
              <label v-if="roleCreate.scenario === 'urgent'">Причина срочности<input v-model="roleCreate.urgentReason" required /></label>
              <template v-if="roleCreate.scenario === 'retroactive'"><label>Результат<select v-model="roleCreate.resultCode"><option value="">Не указан</option><option v-for="option in results" :key="option.code" :value="String(option.code)">{{ option.name }}</option></select></label><label>Отчёт<textarea v-model="roleCreate.report" rows="3"></textarea></label></template>
            </template>
            <p v-if="roleCreateError" class="error" role="alert">{{ roleCreateError }}</p><button :disabled="roleCreateBusy">{{ roleCreateBusy ? "Создаём…" : "Создать карточку" }}</button>
          </form>
        </section>
      </template>

      <template v-else-if="showCard && card">
        <header class="top">
          <div>
            <p class="eyebrow">Внутренняя карточка</p>
            <h1>{{ card.number }}</h1>
          </div>
          <button class="secondary" @click="logout">Выйти</button>
          <a v-if="hasL1Role || hasL2Role" class="button-link" href="/work">Мои карточки</a>
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
              <dt>Часовой пояс отображения</dt><dd>{{ profileTimeZone }}</dd>
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

        <section v-if="canDecide" class="panel actions">
          <h2>{{ hasRole(3) && !isAssignedL2 ? "Решение руководителя" : "Решение L2" }}</h2>
          <div class="action-row">
            <button :disabled="!!actionBusy" @click="confirmCard">{{ actionBusy === "confirm" ? "Сохраняем…" : "Подтвердить назначение" }}</button>
            <form class="inline-form" @submit.prevent="rejectCard">
              <input v-model="rejectionReason" placeholder="Причина отказа" required />
              <button class="danger" :disabled="!!actionBusy">{{ actionBusy === "reject" ? "Сохраняем…" : (hasRole(3) && !isAssignedL2 ? "Отклонить за инженера" : "Отклонить") }}</button>
            </form>
          </div>
        </section>
        <p v-else-if="hasL2Role && card.status === 'assigned'" class="hint">Решение доступно только назначенному инженеру L2.</p>

        <section v-if="canFollowUpAsL1" class="panel actions">
          <h2>Сопровождение L1</h2>
          <button v-if="!card.client_informed" :disabled="!!actionBusy" @click="markClientInformed">{{ actionBusy === "informed" ? "Сохраняем…" : "Клиент проинформирован" }}</button>
          <p v-else class="success">Клиент отмечен как проинформированный.</p>
          <form v-if="card.client_informed" class="inline-form" @submit.prevent="setReminderInterval"><label>Интервал напоминаний<select v-model.number="reminderInterval"><option :value="10">10 минут</option><option :value="30">30 минут</option></select></label><button :disabled="!!actionBusy">Сохранить интервал</button></form>
        </section>
        <p v-else-if="hasL1Role && card.status === 'rejected'" class="hint">Сопровождение доступно только назначенному специалисту L1.</p>

        <section v-if="canReschedule" class="panel actions"><h2>Перенос времени</h2><form class="form reschedule-form" @submit.prevent="rescheduleCard"><label>Новое начало<SlotPicker v-model="rescheduleStart" :min="rescheduleWindow.min" :max="rescheduleWindow.max" required data-test="reschedule-slot" /></label><label>Длительность, минут<input v-model.number="rescheduleDuration" type="number" :min="planning.min_duration_minutes" :max="planning.max_duration_minutes" required /></label><label>Описание<textarea v-model="rescheduleDescription" rows="4"></textarea></label><label>Причина переноса<input v-model="rescheduleReason" required /></label><button :disabled="!!actionBusy">{{ actionBusy === "reschedule" ? "Сохраняем…" : "Сохранить изменения" }}</button></form></section>

        <section v-if="canExecute || canComplete || canEndPendingResult" class="panel actions">
          <h2>Выполнение</h2>
          <button v-if="canExecute && card.status !== 'in_progress'" :disabled="!!actionBusy" @click="startCard">Начать выполнение</button>
          <button v-if="canEndPendingResult" class="secondary" :disabled="!!actionBusy" @click="endPendingResult">{{ actionBusy === "end_pending_result" ? "Сохраняем…" : "Окончить" }}</button>
          <form v-if="canComplete" class="form" @submit.prevent="completeCard">
            <label>Результат<select v-model="completion.resultCode" required><option value="" disabled>Выберите результат</option><option v-for="option in results" :key="option.code" :value="String(option.code)">{{ option.name }}</option></select></label>
            <label>Перечень работ<textarea v-model="completion.report" required rows="4"></textarea></label>
            <label>Фактическая длительность, минут<input v-model="completion.duration" type="number" min="1" /></label>
            <button :disabled="!!actionBusy || !results.length">Завершить</button>
          </form>
        </section>

        <section v-if="canAssign" class="panel actions"><h2>Назначить L2</h2><form class="form" @submit.prevent="assignCard"><label>Инженер<select v-model="assignL2Id" required><option value="" disabled>Выберите инженера</option><option v-for="option in assignmentOptions" :key="option.user_id" :value="String(option.user_id)" :disabled="!option.available">{{ option.display_name }}{{ option.available ? '' : ' — недоступен' }}</option></select></label><label>Комментарий<input v-model="assignmentReason" /></label><button :disabled="!!actionBusy || !assignmentOptions.length">Назначить</button></form></section>
        <section v-if="canSelfAssign" class="panel actions"><h2>Назначить себя L2</h2><form class="form" @submit.prevent="selfAssignCard"><label>Причина назначения<input v-model="assignmentReason" required /></label><button :disabled="!!actionBusy">Назначить себя</button></form></section>

        <section v-if="canCancel" class="panel actions"><h2>Отмена</h2><form class="form" @submit.prevent="cancelCard"><label>Причина отмены<input v-model="cancellationReason" required /></label><button class="danger" :disabled="!!actionBusy">Отменить карточку</button></form></section>

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

        <section class="panel"><h2>Уведомления</h2><p v-if="notificationError" class="muted">{{ notificationError }}</p><p v-else-if="!notifications.length" class="muted">Уведомлений пока нет.</p><ol v-else class="notification-list"><li v-for="(entry, index) in notifications" :key="`${entry.created_at}-${index}`">{{ entry.event }} · {{ entry.channel }} · {{ entry.status }} · {{ formatDateTime(entry.created_at) }}</li></ol></section>

        <footer class="footer muted">Вы вошли как {{ user.full_name || user.username }}.</footer>
      </template>

      <template v-else-if="managerNewPath && !hasRole(3)">
        <h1>Доступ запрещён</h1><p class="error" role="alert">Создание карточек руководителем недоступно для вашей роли (403).</p>
      </template>
      <template v-else-if="managerNewPath">
        <header class="top"><div><p class="eyebrow">RDM</p><h1>Новая карточка</h1><p class="muted">Создание доступно только руководителю.</p></div><a class="button-link" href="/manager">← Вернуться к панели</a></header>
        <p class="hint">Допустимое начало: не раньше чем через 2 часа и не позднее 14 дней. Длительность: 30–720 минут. Часовой пояс: {{ profileTimeZone }}.</p>
        <form class="form create-form" @submit.prevent="submitCreate">
          <label>Номер тикета<input v-model.trim="create.caseNumber" required /></label>
          <button type="button" class="secondary" :disabled="preflightLoading || !create.caseNumber" @click="preflightTicket">{{ preflightLoading ? "Проверяем…" : "Проверить тикет" }}</button>
          <section v-if="ticketPreflight && ticketPreflight.case_number === create.caseNumber" class="panel"><strong>Тикет {{ ticketPreflight.case_number }}</strong><p class="muted">Статус: {{ ticketPreflight.status }} · Клиент: {{ ticketPreflight.client_display_name || "Не указан" }}</p><p v-if="!ticketPreflight.can_create" class="error">Для этого тикета нельзя создать новую активную карточку.</p></section>
          <div class="grid"><label>Начало<SlotPicker v-model="create.start" :min="createMin" :max="createMax" required data-test="create-slot" /></label><label>Длительность, минут<input v-model.number="create.duration" type="number" :min="planning.min_duration_minutes" :max="planning.max_duration_minutes" required /></label></div>
          <label>Описание<textarea v-model="create.description" rows="4"></textarea></label>
          <fieldset><legend>Назначение L2</legend><label class="choice"><input v-model="create.assignment" type="radio" value="auto" /> Автоматически</label><label class="choice"><input v-model="create.assignment" type="radio" value="manual" /> Конкретный L2</label><select v-if="create.assignment === 'manual'" v-model="create.l2UserId" required><option value="" disabled>Выберите L2</option><option v-for="option in l2Options" :key="option.user_id" :value="String(option.user_id)" :disabled="!option.available">{{ option.display_name }}{{ option.available ? "" : ` — ${option.reason_code === "schedule_or_conflict" ? "занят или вне графика" : "недоступен"}` }}</option></select><p v-if="create.assignment === 'manual' && !l2Options.length" class="hint">Укажите время и длительность, чтобы загрузить список L2.</p></fieldset>
          <p v-if="createError" class="error" role="alert">{{ createError }}</p><p v-if="createNotice" class="hint">{{ createNotice }}</p>
          <button type="submit" :disabled="createBusy || preflightLoading || createLoading || !ticketPreflight || ticketPreflight.case_number !== create.caseNumber || !ticketPreflight.can_create">{{ createBusy ? "Создаём…" : "Создать карточку" }}</button>
        </form>
      </template>
      <template v-else-if="managerPath && !manager">
        <h1>Панель руководителя</h1><p v-if="managerError" class="error" role="alert">{{ managerError }}</p><button v-if="hasRole(3)" @click="loadManager">Повторить</button>
      </template>
      <template v-else-if="managerPath && manager">
        <header class="top"><div><p class="eyebrow">RDM</p><h1>Панель руководителя</h1><p class="muted">Часовой пояс: {{ profileTimeZone }}</p></div><div class="top-actions"><a class="button-link" href="/manager/cards/new">+ Создать карточку</a></div></header>
        <div class="manager-stats"><div class="panel"><strong>{{ manager.summary.assigned }}</strong><span>Назначено</span></div><div class="panel"><strong>{{ manager.summary.confirmed }}</strong><span>Подтверждено</span></div><div class="panel"><strong>{{ manager.summary.rejected }}</strong><span>Отклонено</span></div><div class="panel"><strong>{{ manager.summary.overdue }}</strong><span>Просрочено</span></div><div class="panel"><strong>{{ manager.summary.urgent }}</strong><span>Срочно</span></div><div class="panel"><strong>{{ manager.summary.urgent_collision }}</strong><span>Коллизии</span></div></div>
        <section v-if="attentionItems.length" class="panel"><h2>Требуют внимания</h2><div class="work-list"><a v-for="item in attentionItems" :key="item.public_id" class="work-row" :href="`/cards/${item.public_id}`"><strong>{{ item.number }}</strong><span>{{ item.repeated_unsuccessful_cycle ? 'Повторный неуспешный цикл' : item.first_unsuccessful_cycle ? 'Первый неуспешный цикл' : item.status_label }}{{ item.overdue ? ' · Просрочено' : '' }}{{ item.urgent ? ' · Срочно' : '' }}</span></a></div></section>
        <form class="manager-filters panel" @submit.prevent="loadManager"><label>Статус<select v-model="managerStatus"><option value="">Все</option><option value="assigned">Назначено</option><option value="confirmed">Подтверждено</option><option value="rejected">Отклонено</option></select></label><label>Дата с<input v-model="managerFrom" type="date" /></label><label>Дата по<input v-model="managerTo" type="date" /></label><button>Применить</button></form>
        <p v-if="managerError" class="error" role="alert">{{ managerError }} <button class="secondary" @click="loadManager">Повторить</button></p>
        <p v-if="managerLoading" class="hint" role="status">Загрузка календаря…</p>
        <p v-else-if="manager.items.length === manager.limit" class="warning" role="status">Показаны первые {{ manager.limit }} карточек. Данные периода могут быть неполными — сузьте период или фильтр.</p>
        <div class="manager-toggle" role="group" aria-label="Режим отображения"><button type="button" :class="{ selected: managerView === 'list' }" @click="managerView = 'list'">Список</button><button type="button" :class="{ selected: managerView === 'calendar' }" @click="managerView = 'calendar'">Календарь</button><template v-if="managerView === 'calendar'"><button type="button" :class="{ selected: calendarMode === 'day' }" @click="calendarMode = 'day'">День</button><button type="button" :class="{ selected: calendarMode === 'week' }" @click="calendarMode = 'week'">Неделя</button></template></div>
        <p v-if="!managerLoading && !manager.items.length && managerView === 'calendar'" class="hint">Карточек за выбранный период нет — календарь пуст.</p>
        <p v-if="!managerLoading && !manager.items.length && managerView === 'list'" class="hint">Карточки не найдены за выбранный период.</p>
        <div v-else-if="managerView === 'list'" class="manager-list"><a v-for="item in manager.items" :key="item.public_id" class="manager-row panel" :href="`/cards/${item.public_id}`"><div><strong>{{ item.number }}</strong><span class="muted">Тикет {{ item.omnidesk_ticket_number }}</span></div><span class="status" :class="`status-${item.status}`">{{ item.status_label }}</span><span>{{ formatDateTime(item.planned_start_at) }} · {{ formatDuration(item.planned_duration_minutes) }}</span><span>L2: {{ item.l2_engineer_name || "Не назначен" }}</span><span v-if="item.urgent || item.overdue" class="muted">{{ item.urgent ? "Срочно " : "" }}{{ item.overdue ? "Просрочено" : "" }}</span></a></div>
        <template v-else-if="hasCalendarDstTransition">
          <p class="warning dst-notice" role="status">В выбранном периоде часового пояса {{ profileTimeZone }} происходит переход на сезонное время (DST). Карточки отображаются списком для точного отображения времени.</p>
          <p v-if="!calendarPeriodItems.length" class="hint">Карточки не найдены за выбранный период.</p>
          <div v-else class="manager-list"><a v-for="item in calendarPeriodItems" :key="item.public_id" class="manager-row panel" :href="`/cards/${item.public_id}`"><div><strong>{{ item.number }}</strong><span class="muted">Тикет {{ item.omnidesk_ticket_number }}</span></div><span class="status" :class="`status-${item.status}`">{{ item.status_label }}</span><span>{{ formatDateTime(item.planned_start_at) }} · {{ formatDuration(item.planned_duration_minutes) }}</span><span>L2: {{ item.l2_engineer_name || "Не назначен" }}</span><span v-if="item.urgent || item.overdue" class="muted">{{ item.urgent ? "Срочно " : "" }}{{ item.overdue ? "Просрочено" : "" }}</span></a></div>
        </template>
        <div v-else class="calendar" :style="{ '--calendar-days': String(calendarDays.length) }" :data-calendar-days="calendarDays.length"><div class="calendar-head"><span>Время</span><strong v-for="day in calendarDays" :key="day.toISOString()">{{ new Intl.DateTimeFormat("ru-RU", { weekday: "short", day: "numeric", month: "short", timeZone: profileTimeZone }).format(day) }}</strong></div><div class="calendar-body"><div class="calendar-times"><span v-for="hour in calendarHours" :key="hour">{{ String(hour).padStart(2, "0") }}:00</span></div><div v-for="day in calendarDays" :key="`col-${day.toISOString()}`" class="calendar-column"><span v-for="hour in calendarHours" :key="hour" class="calendar-line" :style="{ top: `${hour * 80}px` }"></span><a v-for="item in calendarItems(day)" :key="item.public_id" class="calendar-event" :class="[`status-${item.status}`, { urgent: item.urgent, overdue: item.overdue }]" :style="calendarEventStyle(item, day)" :href="`/cards/${item.public_id}`"><strong>{{ item.number }}</strong><span>{{ formatTime(item.planned_start_at) }}–{{ formatTime(item.planned_end_at) }} · L2: {{ item.l2_engineer_name || "Не назначен" }}</span><small>{{ item.status_label }}{{ item.urgent ? " · Срочно" : "" }}{{ item.overdue ? " · Просрочено" : "" }}</small></a></div></div></div>
      </template>

      <template v-else>
        <h1>RDM</h1>
        <p>Карточка не выбрана.</p>
        <button class="secondary" @click="logout">Выйти</button>
      </template>
    </section>
  </main>
</template>
