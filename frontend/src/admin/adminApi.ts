/**
 * Admin and Reports API client for Milestone FE-04.
 *
 * Implements typed API functions, standardized error handling (401/403/409/422),
 * and same-origin credentialed requests matching existing RDM patterns.
 */

export interface ApiError extends Error {
  status: number;
  detail?: unknown;
}

export interface RoleResponse {
  id: number;
  name: string;
}

export interface AdminUser {
  id: number;
  username: string;
  full_name: string;
  email: string | null;
  phone: string | null;
  omnidesk_staff_id: string | null;
  is_active: boolean;
  roles: RoleResponse[];
  timezone: string;
  telegram_chat_id: string | null;
  bitrix24_user_id: string | null;
  notify_telegram: boolean;
  notify_bitrix24: boolean;
}

export interface UserCreatePayload {
  username: string;
  password: string;
  full_name: string;
  email?: string | null;
  phone?: string | null;
  omnidesk_staff_id?: string | null;
  roles: number[];
  is_active?: boolean;
  telegram_chat_id?: string | null;
  bitrix24_user_id?: string | null;
  notify_telegram?: boolean;
  notify_bitrix24?: boolean;
}

export interface UserUpdatePayload {
  full_name?: string | null;
  email?: string | null;
  phone?: string | null;
  omnidesk_staff_id?: string | null;
  is_active?: boolean | null;
  telegram_chat_id?: string | null;
  bitrix24_user_id?: string | null;
  notify_telegram?: boolean;
  notify_bitrix24?: boolean;
}

export interface ScheduleItem {
  weekday: number; // 1 (Mon) .. 7 (Sun)
  start_time: string; // "HH:MM:SS" or "HH:MM"
  end_time: string;
  timezone: string;
  valid_from?: string | null; // "YYYY-MM-DD"
  valid_to?: string | null;
}

export interface AbsenceItem {
  id: number;
  user_id: number;
  start_at: string; // ISO 8601 with tz
  end_at: string;
  reason: string | null;
  created_by_id?: number | null;
  created_at?: string | null;
}

export interface AbsenceCreatePayload {
  user_id: number;
  start_at: string;
  end_at: string;
  reason?: string | null;
}

export interface CalendarDay {
  date: string; // "YYYY-MM-DD"
  day_type_code: number; // 0=working, 1=weekend, 2=holiday/shortened
  is_manual_override: boolean;
  updated_by_id?: number | null;
  updated_at?: string | null;
  comment?: string | null;
}

export interface CalendarDayUpdatePayload {
  day_type_code: number;
  is_manual_override?: boolean;
  comment?: string | null;
}

export interface DistributionMember {
  id: number;
  user_id: number;
  pool_code: number; // 1=L1, 2=L2
  is_enabled: boolean;
  enabled_by_id?: number | null;
  enabled_at?: string | null;
  disabled_by_id?: number | null;
  disabled_at?: string | null;
  comment?: string | null;
}

export interface DistributionMembershipUpdatePayload {
  pool_code: number;
  enabled: boolean;
  comment: string;
}

export interface PlanningSettings {
  min_lead_minutes: number;
  horizon_days: number;
  default_duration_minutes: number;
  min_duration_minutes: number;
  max_duration_minutes: number;
}

export interface SessionExtensionIntervalResponse {
  interval_seconds: number;
}

export interface PublicNotificationSettings {
  enabled: boolean;
  template: string;
}

export interface CancellationPublicNotificationSettings {
  enabled: boolean;
  template: string;
}

export interface ConnectionResult {
  code: number;
  name: string;
  is_active: boolean;
  sort_order: number;
}

export interface NotificationTemplate {
  code: string;
  channel_code: number | string;
  subject_template?: string | null;
  body_template: string;
  visible: boolean;
}

export interface IntegrationServiceStatus {
  configured: boolean;
  enabled: boolean;
  domain?: string | null;
  bot_username?: string | null;
  details?: Record<string, unknown> | null;
}

export interface IntegrationsStatus {
  omnidesk: IntegrationServiceStatus;
  telegram: IntegrationServiceStatus;
  bitrix24: IntegrationServiceStatus;
  [key: string]: IntegrationServiceStatus;
}

export interface ShareReport {
  numerator: number;
  denominator: number;
  value: number | null;
}

export interface CountReport {
  count: number;
}

export interface SummaryReport {
  created: number;
  completed: number;
  rejected_share: ShareReport;
  repeat_rejected_share: ShareReport;
  overdue: CountReport;
  urgent: CountReport;
  urgent_collisions: CountReport;
}

export interface OverdueCardItem {
  public_id: string;
  number: string;
  status: string;
  status_label: string;
  planned_start_at: string;
}

export interface OverdueCardsResponse {
  items: OverdueCardItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface L2LoadItem {
  user_id: number;
  full_name: string;
  assigned: number;
  completed: number;
  planned_minutes: number;
}

export interface L2LoadResponse {
  items: L2LoadItem[];
}

export const KNOWN_ADMIN_ERRORS: Record<string, string> = {
  user_not_found: "Пользователь не найден.",
  username_already_exists: "Пользователь с таким логином уже существует.",
  omnidesk_staff_id_already_exists: "Сотрудник с таким Omnidesk Staff ID уже привязан к другому пользователю.",
  cannot_deactivate_self: "Нельзя деактивировать собственную учетную запись.",
  cannot_deactivate_last_admin: "Нельзя деактивировать последнего администратора.",
  cannot_remove_last_admin: "Нельзя снять роль администратора с последнего администратора.",
  invalid_role: "Указана недопустимая роль.",
  insufficient_role: "Недостаточно прав для выполнения операции.",
  invalid_timezone: "Указан некорректный часовой пояс IANA.",
  schedule_start_must_precede_end: "Время начала графика должно предшествовать времени окончания.",
  schedule_validity_range_invalid: "Период действия графика указан неверно.",
  schedule_intervals_overlap: "Интервалы графика пересекаются.",
  absence_not_found: "Запись об отсутствии не найдена.",
  datetime_must_be_timezone_aware: "Дата и время должны содержать информацию о часовом поясе.",
  start_at_must_be_before_end_at: "Время начала отсутствия должно быть раньше времени окончания.",
  comment_required: "Укажите обязательное основание (решение руководителя).",
  duration_bounds_inconsistent: "Некорректные границы продолжительности подключения.",
  notification_template_required: "Шаблон уведомления не может быть пустым.",
  session_extension_interval_must_be_whole_minutes: "Интервал автопродления должен быть целым числом минут.",
  period_from_must_be_before_period_to: "Дата начала периода должна быть раньше даты окончания.",
  period_range_exceeds_maximum_366_days: "Диапазон периода не может превышать 366 дней.",
  active_card_exists_for_ticket: "Для этого тикета уже есть активная карточка.",
  invalid_credentials: "Неверный логин или пароль.",
};

export function errorDetail(error: unknown): unknown {
  return (error as ApiError)?.detail;
}

export function validationMessage(detail: unknown): string {
  if (!Array.isArray(detail)) return "Проверьте введённые данные.";
  const messages = detail
    .map((item) => (typeof item === "object" && item !== null ? (item as { msg?: unknown }).msg : null))
    .filter((message): message is string => typeof message === "string");
  return messages.length ? `Проверьте данные: ${messages.join("; ")}` : "Проверьте введённые данные.";
}

export function getErrorMessage(error: unknown): string {
  if (!error) return "Произошла неизвестная ошибка.";
  const err = error as ApiError;
  const detail = err.detail;

  if (typeof detail === "string" && KNOWN_ADMIN_ERRORS[detail]) {
    return KNOWN_ADMIN_ERRORS[detail];
  }
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return validationMessage(detail);
  }
  if (typeof detail === "object" && detail !== null) {
    const detailObj = detail as { detail?: unknown; msg?: unknown; message?: unknown };
    if (typeof detailObj.detail === "string" && KNOWN_ADMIN_ERRORS[detailObj.detail]) {
      return KNOWN_ADMIN_ERRORS[detailObj.detail];
    }
    if (typeof detailObj.detail === "string") {
      return detailObj.detail;
    }
    if (typeof detailObj.message === "string") {
      return detailObj.message;
    }
    if (typeof detailObj.msg === "string") {
      return detailObj.msg;
    }
  }
  if (err.status === 401) return "Требуется авторизация.";
  if (err.status === 403) return "Доступ запрещён (недостаточно прав).";
  if (err.status === 404) return "Запрашиваемый ресурс не найден.";
  if (err.status === 409) return "Конфликт выполнения операции.";
  if (err.status === 422) return "Некорректные параметры запроса.";

  return err.message || "Произошла ошибка при выполнении операции.";
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
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
    const err = new Error(
      typeof payload === "object" && payload !== null && "detail" in payload
        ? String((payload as { detail: unknown }).detail)
        : `Request failed with status ${response.status}`
    ) as ApiError;
    err.status = response.status;
    err.detail = payload && typeof payload === "object" && "detail" in payload ? (payload as { detail: unknown }).detail : payload;
    throw err;
  }

  return payload as T;
}

// ---------------------------------------------------------------------------
// 1. Users API
// ---------------------------------------------------------------------------

export async function listUsers(): Promise<AdminUser[]> {
  return api<AdminUser[]>("/api/v1/admin/users");
}

export async function getUser(userId: number): Promise<AdminUser> {
  return api<AdminUser>(`/api/v1/admin/users/${userId}`);
}

export async function createUser(payload: UserCreatePayload): Promise<AdminUser> {
  return api<AdminUser>("/api/v1/admin/users", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateUser(userId: number, payload: UserUpdatePayload): Promise<AdminUser> {
  return api<AdminUser>(`/api/v1/admin/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function updateUserRoles(userId: number, roles: number[]): Promise<AdminUser> {
  return api<AdminUser>(`/api/v1/admin/users/${userId}/roles`, {
    method: "PUT",
    body: JSON.stringify({ roles }),
  });
}

export async function getUserTimezone(userId: number): Promise<{ user_id: number; timezone: string }> {
  return api<{ user_id: number; timezone: string }>(`/api/v1/auth/users/${userId}/timezone`);
}

export async function updateUserTimezone(userId: number, timezone: string): Promise<{ user_id: number; timezone: string }> {
  return api<{ user_id: number; timezone: string }>(`/api/v1/auth/users/${userId}/timezone`, {
    method: "PUT",
    body: JSON.stringify({ timezone }),
  });
}

// ---------------------------------------------------------------------------
// 2. Schedules & Planning API
// ---------------------------------------------------------------------------

export async function getUserSchedules(userId: number): Promise<ScheduleItem[]> {
  return api<ScheduleItem[]>(`/api/v1/admin/schedules/${userId}`);
}

export async function replaceUserSchedules(userId: number, schedules: ScheduleItem[]): Promise<ScheduleItem[]> {
  return api<ScheduleItem[]>(`/api/v1/admin/schedules/${userId}`, {
    method: "PUT",
    body: JSON.stringify(schedules),
  });
}

export async function listAbsences(params?: {
  user_id?: number;
  period_from?: string;
  period_to?: string;
}): Promise<AbsenceItem[]> {
  const query = new URLSearchParams();
  if (params?.user_id) query.set("user_id", String(params.user_id));
  if (params?.period_from) query.set("period_from", params.period_from);
  if (params?.period_to) query.set("period_to", params.period_to);
  const qStr = query.toString();
  return api<AbsenceItem[]>(`/api/v1/admin/absences${qStr ? `?${qStr}` : ""}`);
}

export async function createAbsence(payload: AbsenceCreatePayload): Promise<AbsenceItem> {
  return api<AbsenceItem>("/api/v1/admin/absences", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteAbsence(absenceId: number): Promise<{ deleted: boolean; id: number }> {
  return api<{ deleted: boolean; id: number }>(`/api/v1/admin/absences/${absenceId}`, {
    method: "DELETE",
  });
}

export async function getCalendarDays(params: {
  from_date?: string;
  to_date?: string;
  period_from?: string;
  period_to?: string;
}): Promise<CalendarDay[]> {
  const query = new URLSearchParams();
  if (params.from_date) query.set("from_date", params.from_date);
  if (params.to_date) query.set("to_date", params.to_date);
  if (params.period_from) query.set("period_from", params.period_from);
  if (params.period_to) query.set("period_to", params.period_to);
  return api<CalendarDay[]>(`/api/v1/admin/calendar/days?${query.toString()}`);
}

export async function updateCalendarDay(calendarDate: string, payload: CalendarDayUpdatePayload): Promise<CalendarDay> {
  return api<CalendarDay>(`/api/v1/admin/calendar/days/${calendarDate}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function listDistributionMembers(poolCode?: number): Promise<DistributionMember[]> {
  const q = poolCode ? `?pool_code=${poolCode}` : "";
  return api<DistributionMember[]>(`/api/v1/admin/distribution/members${q}`);
}

export async function updateDistributionMembership(
  userId: number,
  payload: DistributionMembershipUpdatePayload
): Promise<DistributionMember> {
  return api<DistributionMember>(`/api/v1/admin/distribution/members/${userId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function getPlanningSettings(): Promise<PlanningSettings> {
  return api<PlanningSettings>("/api/v1/admin/settings/planning");
}

export async function updatePlanningSettings(payload: PlanningSettings): Promise<PlanningSettings> {
  return api<PlanningSettings>("/api/v1/admin/settings/planning", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// 3. Settings, Catalog & Integrations API
// ---------------------------------------------------------------------------

export async function getSessionExtensionInterval(): Promise<SessionExtensionIntervalResponse> {
  return api<SessionExtensionIntervalResponse>("/api/v1/manager/settings/session-extension-interval");
}

export async function updateSessionExtensionInterval(interval_seconds: number): Promise<SessionExtensionIntervalResponse> {
  return api<SessionExtensionIntervalResponse>("/api/v1/manager/settings/session-extension-interval", {
    method: "PUT",
    body: JSON.stringify({ interval_seconds }),
  });
}

export async function getPublicNotificationSettings(): Promise<PublicNotificationSettings> {
  return api<PublicNotificationSettings>("/api/v1/manager/settings/public-notification");
}

export async function updatePublicNotificationSettings(payload: PublicNotificationSettings): Promise<PublicNotificationSettings> {
  return api<PublicNotificationSettings>("/api/v1/manager/settings/public-notification", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function getCancellationPublicNotificationSettings(): Promise<CancellationPublicNotificationSettings> {
  return api<CancellationPublicNotificationSettings>("/api/v1/manager/settings/cancellation-public-notification");
}

export async function updateCancellationPublicNotificationSettings(
  payload: CancellationPublicNotificationSettings
): Promise<CancellationPublicNotificationSettings> {
  return api<CancellationPublicNotificationSettings>("/api/v1/manager/settings/cancellation-public-notification", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function getConnectionResults(): Promise<ConnectionResult[]> {
  return api<ConnectionResult[]>("/api/v1/admin/results");
}

export async function createConnectionResult(payload: ConnectionResult): Promise<ConnectionResult> {
  return api<ConnectionResult>("/api/v1/admin/results", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateConnectionResult(code: number, payload: Partial<ConnectionResult>): Promise<ConnectionResult> {
  return api<ConnectionResult>(`/api/v1/admin/results/${code}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function getNotificationTemplates(): Promise<NotificationTemplate[]> {
  return api<NotificationTemplate[]>("/api/v1/admin/notification-templates");
}

export async function updateNotificationTemplate(
  code: string,
  payload: Partial<NotificationTemplate>
): Promise<NotificationTemplate> {
  return api<NotificationTemplate>(`/api/v1/admin/notification-templates/${code}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function getIntegrationsStatus(): Promise<IntegrationsStatus> {
  return api<IntegrationsStatus>("/api/v1/admin/integrations/status");
}

// ---------------------------------------------------------------------------
// 4. Reports API
// ---------------------------------------------------------------------------

export async function getReportsSummary(fromIso: string, toIso: string): Promise<SummaryReport> {
  return api<SummaryReport>(`/api/v1/reports/summary?from=${encodeURIComponent(fromIso)}&to=${encodeURIComponent(toIso)}`);
}

export async function getReportsOverdue(
  fromIso: string,
  toIso: string,
  limit = 50,
  offset = 0
): Promise<OverdueCardsResponse> {
  return api<OverdueCardsResponse>(
    `/api/v1/reports/overdue?from=${encodeURIComponent(fromIso)}&to=${encodeURIComponent(toIso)}&limit=${limit}&offset=${offset}`
  );
}

export async function getReportsL2Load(fromIso: string, toIso: string): Promise<L2LoadResponse> {
  return api<L2LoadResponse>(`/api/v1/reports/l2-load?from=${encodeURIComponent(fromIso)}&to=${encodeURIComponent(toIso)}`);
}
