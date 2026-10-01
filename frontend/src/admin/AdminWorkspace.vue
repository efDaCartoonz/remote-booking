<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import ScheduleEditor from "./ScheduleEditor.vue";
import { quarterHourOptions } from "./scheduleGenerator";
import {
  AdminUser,
  AbsenceItem,
  CalendarDay,
  DistributionMember,
  getErrorMessage,
  getIntegrationsStatus,
  getNotificationTemplates,
  getPlanningSettings,
  getPublicNotificationSettings,
  getCancellationPublicNotificationSettings,
  getSessionExtensionInterval,
  IntegrationsStatus,
  listAbsences,
  listDistributionMembers,
  listUsers,
  NotificationTemplate,
  PlanningSettings,
  PublicNotificationSettings,
  CancellationPublicNotificationSettings,
  updateCalendarDay,
  updateCancellationPublicNotificationSettings,
  updateDistributionMembership,
  updateNotificationTemplate,
  updatePlanningSettings,
  updatePublicNotificationSettings,
  updateSessionExtensionInterval,
  updateUser,
  updateUserRoles,
  updateUserTimezone,
  createUser,
  createAbsence,
  deleteAbsence,
  getCalendarDays,
  UserUpdatePayload,
} from "./adminApi";
import {
  formatDateInTz,
  formatDateTimeInTz,
  getTodayDateString,
  STANDARD_TIMEZONES,
} from "./adminTimezone";
import { convertWallTimeToISO } from "../frame/timezone";

interface Props {
  currentUser?: {
    id: number;
    username?: string;
    full_name?: string;
    roles: Array<number | { id: number; name?: string }>;
  } | null;
  timezone?: string;
}

const props = withDefaults(defineProps<Props>(), {
  currentUser: null,
  timezone: "Asia/Yekaterinburg",
});

const profileTz = computed(() => props.timezone || "Asia/Yekaterinburg");

const isAdmin = computed(() => {
  if (!props.currentUser || !Array.isArray(props.currentUser.roles)) return false;
  return props.currentUser.roles.some((r) => {
    const roleId = typeof r === "number" ? r : r.id;
    return roleId === 4;
  });
});

type TabKey = "users" | "schedules" | "settings";
const currentTab = ref<TabKey>("users");

// Global alerts
const actionError = ref("");
const actionSuccess = ref("");
const busy = ref(false);

function clearAlerts() {
  actionError.value = "";
  actionSuccess.value = "";
}

// ---------------------------------------------------------------------------
// 1. Users Tab State
// ---------------------------------------------------------------------------
const users = ref<AdminUser[]>([]);
const usersSearch = ref("");
const showCreateUserModal = ref(false);
const editingUser = ref<AdminUser | null>(null);

// Create user state (password is cleared immediately upon submit)
const newUsername = ref("");
const newPassword = ref("");
const newFullName = ref("");
const newEmail = ref("");
const newPhone = ref("");
const newStaffId = ref("");
const newRoles = ref<number[]>([1]); // default L1
const newTimezone = ref(profileTz.value);
const newIsActive = ref(true);
const newTelegramChatId = ref("");
const newBitrix24UserId = ref("");
const newNotifyTelegram = ref(true);
const newNotifyBitrix24 = ref(true);

// Edit user state
const editFullName = ref("");
const editEmail = ref("");
const editPhone = ref("");
const editStaffId = ref("");
const editIsActive = ref(true);
const editRoles = ref<number[]>([]);
const editUserTz = ref("");
const editTelegramChatId = ref("");
const editBitrix24UserId = ref("");
const editNotifyTelegram = ref(true);
const editNotifyBitrix24 = ref(true);

const filteredUsers = computed(() => {
  if (!usersSearch.value.trim()) return users.value;
  const q = usersSearch.value.toLowerCase().trim();
  return users.value.filter(
    (u) =>
      u.full_name.toLowerCase().includes(q) ||
      u.username.toLowerCase().includes(q) ||
      (u.email && u.email.toLowerCase().includes(q))
  );
});

async function loadUsers() {
  if (!isAdmin.value) return;
  busy.value = true;
  try {
    users.value = await listUsers();
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

function openCreateUser() {
  clearAlerts();
  newUsername.value = "";
  newPassword.value = "";
  newFullName.value = "";
  newEmail.value = "";
  newPhone.value = "";
  newStaffId.value = "";
  newRoles.value = [1];
  newTimezone.value = profileTz.value;
  newIsActive.value = true;
  newTelegramChatId.value = "";
  newBitrix24UserId.value = "";
  newNotifyTelegram.value = true;
  newNotifyBitrix24.value = true;
  showCreateUserModal.value = true;
}

async function handleCreateUser() {
  clearAlerts();
  if (!newUsername.value.trim() || !newPassword.value || !newFullName.value.trim()) {
    actionError.value = "Укажите логин, пароль и ФИО пользователя.";
    return;
  }
  if (newPassword.value.length < 8) {
    actionError.value = "Пароль должен содержать не менее 8 символов.";
    return;
  }
  if (newRoles.value.length === 0) {
    actionError.value = "Выберите хотя бы одну роль.";
    return;
  }

  // Extract password and wipe from component state immediately
  const passwordToSend = newPassword.value;
  newPassword.value = "";

  busy.value = true;
  try {
    const created = await createUser({
      username: newUsername.value.trim(),
      password: passwordToSend,
      full_name: newFullName.value.trim(),
      email: newEmail.value.trim() || null,
      phone: newPhone.value.trim() || null,
      omnidesk_staff_id: newStaffId.value.trim() || null,
      roles: newRoles.value,
      is_active: newIsActive.value,
      telegram_chat_id: newTelegramChatId.value.trim() || null,
      bitrix24_user_id: newBitrix24UserId.value.trim() || null,
      notify_telegram: newNotifyTelegram.value,
      notify_bitrix24: newNotifyBitrix24.value,
    });

    if (newTimezone.value && newTimezone.value !== "Asia/Yekaterinburg") {
      try {
        await updateUserTimezone(created.id, newTimezone.value);
      } catch {
        // Non-critical timezone update error
      }
    }

    actionSuccess.value = `Пользователь ${created.username} успешно создан.`;
    showCreateUserModal.value = false;
    await loadUsers();
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

function startEditUser(user: AdminUser) {
  clearAlerts();
  editingUser.value = user;
  editFullName.value = user.full_name;
  editEmail.value = user.email || "";
  editPhone.value = user.phone || "";
  editStaffId.value = user.omnidesk_staff_id ?? "";
  editIsActive.value = user.is_active;
  editRoles.value = user.roles.map((r) => r.id);
  editUserTz.value = user.timezone || profileTz.value;
  editTelegramChatId.value = user.telegram_chat_id ?? "";
  editBitrix24UserId.value = user.bitrix24_user_id ?? "";
  editNotifyTelegram.value = user.notify_telegram !== false;
  editNotifyBitrix24.value = user.notify_bitrix24 !== false;
}

function cancelEditUser() {
  editingUser.value = null;
}

async function handleSaveUser() {
  if (!editingUser.value) return;
  clearAlerts();

  if (editingUser.value.is_active && !editIsActive.value) {
    const confirmed = window.confirm(
      `Вы действительно хотите деактивировать пользователя ${editingUser.value.username}?`
    );
    if (!confirmed) return;
  }

  if (editRoles.value.length === 0) {
    actionError.value = "У пользователя должна быть хотя бы одна роль.";
    return;
  }

  const payload: UserUpdatePayload = {};

  const nextFullName = editFullName.value.trim() || null;
  if (nextFullName !== (editingUser.value.full_name || null)) {
    payload.full_name = nextFullName;
  }

  const nextEmail = editEmail.value.trim() || null;
  if (nextEmail !== (editingUser.value.email || null)) {
    payload.email = nextEmail;
  }

  const nextPhone = editPhone.value.trim() || null;
  if (nextPhone !== (editingUser.value.phone || null)) {
    payload.phone = nextPhone;
  }

  const nextStaffId = editStaffId.value.trim() || null;
  if (nextStaffId !== (editingUser.value.omnidesk_staff_id ?? null)) {
    payload.omnidesk_staff_id = nextStaffId;
  }

  if (editIsActive.value !== editingUser.value.is_active) {
    payload.is_active = editIsActive.value;
  }

  const nextTg = editTelegramChatId.value.trim() || null;
  if (nextTg !== (editingUser.value.telegram_chat_id ?? null)) {
    payload.telegram_chat_id = nextTg;
  }

  const nextB24 = editBitrix24UserId.value.trim() || null;
  if (nextB24 !== (editingUser.value.bitrix24_user_id ?? null)) {
    payload.bitrix24_user_id = nextB24;
  }

  if (editNotifyTelegram.value !== (editingUser.value.notify_telegram !== false)) {
    payload.notify_telegram = editNotifyTelegram.value;
  }

  if (editNotifyBitrix24.value !== (editingUser.value.notify_bitrix24 !== false)) {
    payload.notify_bitrix24 = editNotifyBitrix24.value;
  }

  busy.value = true;
  try {
    const userId = editingUser.value.id;
    if (Object.keys(payload).length > 0) {
      await updateUser(userId, payload);
    }

    const currentRoleIds = editingUser.value.roles.map((r) => r.id).sort();
    const nextRoleIds = [...editRoles.value].sort();
    const rolesChanged =
      currentRoleIds.length !== nextRoleIds.length ||
      currentRoleIds.some((id, idx) => id !== nextRoleIds[idx]);

    if (rolesChanged) {
      await updateUserRoles(userId, editRoles.value);
    }

    if (editUserTz.value && editUserTz.value !== editingUser.value.timezone) {
      await updateUserTimezone(userId, editUserTz.value);
    }

    actionSuccess.value = `Данные пользователя ${editingUser.value.username} обновлены.`;
    editingUser.value = null;
    await loadUsers();
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

// ---------------------------------------------------------------------------
// 2. Schedules & Planning Tab State
// ---------------------------------------------------------------------------
type ScheduleSubTab = "schedules" | "absences" | "calendar" | "distribution" | "planning";
const currentScheduleSubTab = ref<ScheduleSubTab>("schedules");

// Absences subtab
const absences = ref<AbsenceItem[]>([]);
const absenceUserIdFilter = ref<number | "">("");
const absenceNewUserId = ref<number | "">("");
const timeOptions = quarterHourOptions();
const absenceStartDate = ref(getTodayDateString());
const absenceStartTime = ref("00:00");
const absenceEndDate = ref(getTodayDateString());
const absenceEndTime = ref("23:59");
const absenceReason = ref("");

async function loadAbsences() {
  busy.value = true;
  try {
    absences.value = await listAbsences(
      absenceUserIdFilter.value && typeof absenceUserIdFilter.value === "number"
        ? { user_id: absenceUserIdFilter.value }
        : undefined
    );
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function handleCreateAbsence() {
  clearAlerts();
  if (!absenceNewUserId.value || typeof absenceNewUserId.value !== "number") {
    actionError.value = "Выберите сотрудника.";
    return;
  }
  if (!absenceStartDate.value || !absenceStartTime.value || !absenceEndDate.value || !absenceEndTime.value) {
    actionError.value = "Укажите даты и время начала и окончания отсутствия.";
    return;
  }

  busy.value = true;
  try {
    const startIso = convertWallTimeToISO(absenceStartDate.value, absenceStartTime.value, profileTz.value);
    const endIso = convertWallTimeToISO(absenceEndDate.value, absenceEndTime.value, profileTz.value);

    await createAbsence({
      user_id: absenceNewUserId.value,
      start_at: startIso,
      end_at: endIso,
      reason: absenceReason.value.trim() || null,
    });

    actionSuccess.value = "Отсутствие сотрудника успешно зафиксировано.";
    absenceReason.value = "";
    await loadAbsences();
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function handleDeleteAbsence(absenceId: number) {
  const confirmed = window.confirm("Удалить запись об отсутствии?");
  if (!confirmed) return;
  clearAlerts();
  busy.value = true;
  try {
    await deleteAbsence(absenceId);
    actionSuccess.value = "Запись об отсутствии удалена.";
    await loadAbsences();
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

// Production Calendar subtab
const calendarFromDate = ref(getTodayDateString());
const calendarToDate = ref(getTodayDateString());
const calendarDays = ref<CalendarDay[]>([]);
const editCalendarDate = ref("");
const editDayTypeCode = ref(0);
const editCalendarComment = ref("");

async function loadCalendarDays() {
  busy.value = true;
  try {
    calendarDays.value = await getCalendarDays({
      from_date: calendarFromDate.value,
      to_date: calendarToDate.value,
    });
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

function startEditCalendarDay(day: CalendarDay) {
  editCalendarDate.value = day.date;
  editDayTypeCode.value = day.day_type_code;
  editCalendarComment.value = day.comment || "";
}

async function handleSaveCalendarDay() {
  if (!editCalendarDate.value) return;
  clearAlerts();
  busy.value = true;
  try {
    await updateCalendarDay(editCalendarDate.value, {
      day_type_code: editDayTypeCode.value,
      is_manual_override: true,
      comment: editCalendarComment.value.trim() || null,
    });
    actionSuccess.value = `Производственный календарь на ${editCalendarDate.value} обновлён.`;
    editCalendarDate.value = "";
    await loadCalendarDays();
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

// Distribution Membership subtab
const distributionPoolFilter = ref<number | "">("");
const distributionMembers = ref<DistributionMember[]>([]);
const distEditMember = ref<DistributionMember | null>(null);
const distEditEnabled = ref(false);
const distEditComment = ref("");

async function loadDistributionMembers() {
  busy.value = true;
  try {
    distributionMembers.value = await listDistributionMembers(
      typeof distributionPoolFilter.value === "number" ? distributionPoolFilter.value : undefined
    );
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

function startEditDistribution(member: DistributionMember) {
  distEditMember.value = member;
  distEditEnabled.value = !member.is_enabled;
  distEditComment.value = "";
}

async function handleSaveDistribution() {
  if (!distEditMember.value) return;
  clearAlerts();
  if (!distEditComment.value.trim()) {
    actionError.value = "Укажите обязательное основание (решение руководителя).";
    return;
  }

  busy.value = true;
  try {
    await updateDistributionMembership(distEditMember.value.user_id, {
      pool_code: distEditMember.value.pool_code,
      enabled: distEditEnabled.value,
      comment: distEditComment.value.trim(),
    });
    actionSuccess.value = "Участие сотрудника в распределении обновлено.";
    distEditMember.value = null;
    await loadDistributionMembers();
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

// Planning Settings subtab
const planningSettings = ref<PlanningSettings>({
  min_lead_minutes: 120,
  horizon_days: 14,
  default_duration_minutes: 60,
  min_duration_minutes: 30,
  max_duration_minutes: 720,
});

async function loadPlanningSettings() {
  busy.value = true;
  try {
    planningSettings.value = await getPlanningSettings();
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function handleSavePlanningSettings() {
  clearAlerts();
  busy.value = true;
  try {
    planningSettings.value = await updatePlanningSettings(planningSettings.value);
    actionSuccess.value = "Параметры планирования успешно сохранены.";
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

// ---------------------------------------------------------------------------
// 3. Settings & Integrations Tab State
// ---------------------------------------------------------------------------
type SettingsSubTab = "session" | "templates" | "integrations";
const currentSettingsSubTab = ref<SettingsSubTab>("session");

// Session & Notification Settings
const extensionMinutes = ref(15);
const publicWarnSettings = ref<PublicNotificationSettings>({ enabled: true, template: "" });
const cancelNotificationSettings = ref<CancellationPublicNotificationSettings>({ enabled: true, template: "" });

async function loadGeneralSettings() {
  busy.value = true;
  try {
    const [ext, pubWarn, cancelNotif] = await Promise.all([
      getSessionExtensionInterval(),
      getPublicNotificationSettings(),
      getCancellationPublicNotificationSettings(),
    ]);
    extensionMinutes.value = Math.round(ext.interval_seconds / 60);
    publicWarnSettings.value = pubWarn;
    cancelNotificationSettings.value = cancelNotif;
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function handleSaveExtensionInterval() {
  clearAlerts();
  busy.value = true;
  try {
    const res = await updateSessionExtensionInterval(extensionMinutes.value * 60);
    extensionMinutes.value = Math.round(res.interval_seconds / 60);
    actionSuccess.value = "Интервал автопродления сессии сохранён.";
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function handleSavePublicWarning() {
  clearAlerts();
  busy.value = true;
  try {
    publicWarnSettings.value = await updatePublicNotificationSettings(publicWarnSettings.value);
    actionSuccess.value = "Настройки 15-минутного предупреждения клиента сохранены.";
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function handleSaveCancellationWarning() {
  clearAlerts();
  busy.value = true;
  try {
    cancelNotificationSettings.value = await updateCancellationPublicNotificationSettings(
      cancelNotificationSettings.value
    );
    actionSuccess.value = "Настройки публичного сообщения об отмене сохранены.";
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

// Notification Templates subtab
const notificationTemplates = ref<NotificationTemplate[]>([]);
const editingTemplate = ref<NotificationTemplate | null>(null);
const editTemplateSubject = ref("");
const editTemplateBody = ref("");
const editTemplateVisible = ref(true);

async function loadNotificationTemplates() {
  busy.value = true;
  try {
    notificationTemplates.value = await getNotificationTemplates();
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

function startEditTemplate(tpl: NotificationTemplate) {
  editingTemplate.value = tpl;
  editTemplateSubject.value = tpl.subject_template || "";
  editTemplateBody.value = tpl.body_template || "";
  editTemplateVisible.value = tpl.visible;
}

async function handleSaveTemplate() {
  if (!editingTemplate.value) return;
  clearAlerts();
  busy.value = true;
  try {
    await updateNotificationTemplate(editingTemplate.value.code, {
      subject_template: editTemplateSubject.value || null,
      body_template: editTemplateBody.value,
      visible: editTemplateVisible.value,
    });
    actionSuccess.value = `Шаблон ${editingTemplate.value.code} обновлён.`;
    editingTemplate.value = null;
    await loadNotificationTemplates();
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

// Integrations subtab (Read-Only)
const integrationsStatus = ref<IntegrationsStatus | null>(null);

async function loadIntegrationsStatus() {
  busy.value = true;
  try {
    integrationsStatus.value = await getIntegrationsStatus();
  } catch (err) {
    actionError.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

// Tab switcher & initial loader
watch(
  () => currentTab.value,
  async (tab) => {
    if (!isAdmin.value) return;
    if (tab === "users") await loadUsers();
    else if (tab === "schedules") {
      if (users.value.length === 0) await loadUsers();
      if (currentScheduleSubTab.value === "absences") await loadAbsences();
      else if (currentScheduleSubTab.value === "calendar") await loadCalendarDays();
      else if (currentScheduleSubTab.value === "distribution") await loadDistributionMembers();
      else if (currentScheduleSubTab.value === "planning") await loadPlanningSettings();
    } else if (tab === "settings") {
      if (currentSettingsSubTab.value === "session") await loadGeneralSettings();
      else if (currentSettingsSubTab.value === "templates") await loadNotificationTemplates();
      else if (currentSettingsSubTab.value === "integrations") await loadIntegrationsStatus();
    }
  }
);

watch(
  () => currentScheduleSubTab.value,
  async (subTab) => {
    if (!isAdmin.value || currentTab.value !== "schedules") return;
    if (subTab === "absences") await loadAbsences();
    else if (subTab === "calendar") await loadCalendarDays();
    else if (subTab === "distribution") await loadDistributionMembers();
    else if (subTab === "planning") await loadPlanningSettings();
  }
);

watch(
  () => currentSettingsSubTab.value,
  async (subTab) => {
    if (!isAdmin.value || currentTab.value !== "settings") return;
    if (subTab === "session") await loadGeneralSettings();
    else if (subTab === "templates") await loadNotificationTemplates();
    else if (subTab === "integrations") await loadIntegrationsStatus();
  }
);

onMounted(async () => {
  if (isAdmin.value) {
    await loadUsers();
  }
});

const weekdayNames = [
  "Понедельник",
  "Вторник",
  "Среда",
  "Четверг",
  "Пятница",
  "Суббота",
  "Воскресенье",
];

const dayTypeNames: Record<number, string> = {
  0: "Рабочий день",
  1: "Выходной день",
  2: "Праздничный / сокращённый",
};

function getUserDisplayName(userId: number): string {
  const u = users.value.find((x) => x.id === userId);
  return u ? `${u.full_name} (@${u.username})` : `ID ${userId}`;
}
</script>

<template>
  <div v-if="isAdmin" class="admin-workspace">
    <div class="top">
      <div>
        <p class="eyebrow">Администрирование RDM</p>
        <h1>Панель администратора</h1>
      </div>
    </div>

    <!-- Global Alerts -->
    <div v-if="actionError" class="panel warning error" data-test="admin-error">
      {{ actionError }}
    </div>
    <div v-if="actionSuccess" class="panel warning success" data-test="admin-success">
      {{ actionSuccess }}
    </div>

    <!-- Main Admin Navigation Tabs -->
    <div class="manager-toggle admin-tabs">
      <button
        type="button"
        :class="{ selected: currentTab === 'users' }"
        data-test="tab-users"
        @click="currentTab = 'users'"
      >
        Пользователи
      </button>
      <button
        type="button"
        :class="{ selected: currentTab === 'schedules' }"
        data-test="tab-schedules"
        @click="currentTab = 'schedules'"
      >
        Графики и планирование
      </button>
      <button
        type="button"
        :class="{ selected: currentTab === 'settings' }"
        data-test="tab-settings"
        @click="currentTab = 'settings'"
      >
        Настройки и интеграции
      </button>
    </div>

    <!-- ===================================================================== -->
    <!-- 1. USERS TAB -->
    <!-- ===================================================================== -->
    <section v-if="currentTab === 'users'" class="panel admin-section" data-test="section-users">
      <div class="section-heading">
        <h2>Управление пользователями и ролями</h2>
        <button type="button" data-test="btn-open-create-user" @click="openCreateUser">
          + Создать пользователя
        </button>
      </div>

      <div class="manager-filters">
        <label>
          <span>Поиск пользователей:</span>
          <input
            v-model="usersSearch"
            type="search"
            placeholder="Поиск по ФИО, логину или email..."
            data-test="input-users-search"
          />
        </label>
      </div>

      <!-- Create User Modal / Panel -->
      <div v-if="showCreateUserModal" class="panel actions create-user-box" data-test="modal-create-user">
        <h3>Новый пользователь</h3>
        <form class="form" @submit.prevent="handleCreateUser">
          <div class="grid">
            <label>
              <span>Логин (username) *</span>
              <input v-model="newUsername" type="text" required data-test="input-new-username" />
            </label>
            <label>
              <span>Пароль *</span>
              <input
                v-model="newPassword"
                type="password"
                required
                minlength="8"
                maxlength="128"
                autocomplete="new-password"
                data-test="input-new-password"
              />
            </label>
          </div>

          <div class="grid">
            <label>
              <span>ФИО сотрудника *</span>
              <input v-model="newFullName" type="text" required data-test="input-new-fullname" />
            </label>
            <label>
              <span>Email</span>
              <input v-model="newEmail" type="email" data-test="input-new-email" />
            </label>
          </div>

          <div class="grid">
            <label>
              <span>Телефон</span>
              <input v-model="newPhone" type="tel" data-test="input-new-phone" />
            </label>
            <label>
              <span>Omnidesk Staff ID</span>
              <input v-model.trim="newStaffId" inputmode="numeric" pattern="[0-9]*" data-test="input-new-staff-id" />
            </label>
          </div>

          <div class="grid">
            <label>
              <span>Часовой пояс профиля</span>
              <select v-model="newTimezone" data-test="select-new-timezone">
                <option v-for="tz in STANDARD_TIMEZONES" :key="tz" :value="tz">{{ tz }}</option>
              </select>
            </label>
            <label class="choice">
              <input v-model="newIsActive" type="checkbox" data-test="checkbox-new-active" />
              <span>Учётная запись активна</span>
            </label>
          </div>

          <fieldset>
            <legend>Роли пользователя *</legend>
            <div class="action-row">
              <label class="choice">
                <input v-model="newRoles" type="checkbox" :value="1" data-test="role-1" />
                <span>Специалист L1</span>
              </label>
              <label class="choice">
                <input v-model="newRoles" type="checkbox" :value="2" data-test="role-2" />
                <span>Инженер L2</span>
              </label>
              <label class="choice">
                <input v-model="newRoles" type="checkbox" :value="3" data-test="role-3" />
                <span>Руководитель</span>
              </label>
              <label class="choice">
                <input v-model="newRoles" type="checkbox" :value="4" data-test="role-4" />
                <span>Администратор</span>
              </label>
            </div>
          </fieldset>

          <div class="action-row">
            <button type="submit" :disabled="busy" data-test="btn-submit-create-user">
              Создать
            </button>
            <button
              type="button"
              class="secondary"
              data-test="btn-cancel-create-user"
              @click="showCreateUserModal = false"
            >
              Отмена
            </button>
          </div>
        </form>
      </div>

      <!-- Edit User Panel -->
      <div v-if="editingUser" class="panel actions edit-user-box" data-test="modal-edit-user">
        <h3>Редактирование: {{ editingUser.username }} (ID {{ editingUser.id }})</h3>
        <form class="form" @submit.prevent="handleSaveUser">
          <div class="grid">
            <label>
              <span>ФИО сотрудника</span>
              <input v-model="editFullName" type="text" required data-test="input-edit-fullname" />
            </label>
            <label>
              <span>Email</span>
              <input v-model="editEmail" type="email" data-test="input-edit-email" />
            </label>
          </div>

          <div class="grid">
            <label>
              <span>Телефон</span>
              <input v-model="editPhone" type="tel" data-test="input-edit-phone" />
            </label>
            <label>
              <span>Omnidesk Staff ID</span>
              <input v-model.trim="editStaffId" inputmode="numeric" pattern="[0-9]*" data-test="input-edit-staff-id" />
            </label>
          </div>

          <div class="grid">
            <label>
              <span>Telegram chat ID</span>
              <input
                v-model.trim="editTelegramChatId"
                type="text"
                placeholder="ID чата (цифры, опц. минус в начале)"
                data-test="input-edit-telegram-chat-id"
              />
            </label>
            <label>
              <span>Bitrix24 user ID</span>
              <input
                v-model.trim="editBitrix24UserId"
                type="text"
                inputmode="numeric"
                pattern="[0-9]*"
                placeholder="ID пользователя (цифры)"
                data-test="input-edit-bitrix24-user-id"
              />
            </label>
          </div>

          <fieldset>
            <legend>Каналы уведомлений</legend>
            <div class="action-row">
              <label class="choice">
                <input
                  v-model="editNotifyTelegram"
                  type="checkbox"
                  data-test="checkbox-edit-notify-telegram"
                />
                <span>Уведомлять в Telegram</span>
              </label>
              <label class="choice">
                <input
                  v-model="editNotifyBitrix24"
                  type="checkbox"
                  data-test="checkbox-edit-notify-bitrix24"
                />
                <span>Уведомлять в Битрикс24</span>
              </label>
            </div>
          </fieldset>

          <div class="grid">
            <label>
              <span>Часовой пояс</span>
              <select v-model="editUserTz" data-test="select-edit-timezone">
                <option v-for="tz in STANDARD_TIMEZONES" :key="tz" :value="tz">{{ tz }}</option>
              </select>
            </label>
            <label class="choice">
              <input v-model="editIsActive" type="checkbox" data-test="checkbox-edit-active" />
              <span>Активен</span>
            </label>
          </div>

          <fieldset>
            <legend>Роли пользователя</legend>
            <div class="action-row">
              <label class="choice">
                <input v-model="editRoles" type="checkbox" :value="1" />
                <span>L1</span>
              </label>
              <label class="choice">
                <input v-model="editRoles" type="checkbox" :value="2" />
                <span>L2</span>
              </label>
              <label class="choice">
                <input v-model="editRoles" type="checkbox" :value="3" />
                <span>Руководитель</span>
              </label>
              <label class="choice">
                <input v-model="editRoles" type="checkbox" :value="4" />
                <span>Администратор</span>
              </label>
            </div>
          </fieldset>

          <div class="action-row">
            <button type="submit" :disabled="busy" data-test="btn-save-edit-user">
              Сохранить изменения
            </button>
            <button type="button" class="secondary" @click="cancelEditUser">
              Отмена
            </button>
          </div>
        </form>
      </div>

      <!-- Users Table -->
      <div class="table-wrap">
        <table class="admin-table" data-test="table-users">
          <thead>
            <tr>
              <th>ID</th>
              <th>Логин</th>
              <th>ФИО</th>
              <th>Email / Телефон</th>
              <th>Omnidesk ID</th>
              <th>Каналы уведомлений</th>
              <th>Роли</th>
              <th>Часовой пояс</th>
              <th>Статус</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in filteredUsers" :key="u.id" :data-test="`user-row-${u.id}`">
              <td>{{ u.id }}</td>
              <td><strong>{{ u.username }}</strong></td>
              <td>{{ u.full_name }}</td>
              <td>
                <div>{{ u.email || '—' }}</div>
                <small class="muted">{{ u.phone || '' }}</small>
              </td>
              <td>{{ u.omnidesk_staff_id ?? '—' }}</td>
              <td>
                <div>Telegram: {{ u.telegram_chat_id ? 'настроен' : 'нет' }}</div>
                <div>Битрикс24: {{ u.bitrix24_user_id ? 'настроен' : 'нет' }}</div>
              </td>
              <td>
                <span
                  v-for="r in u.roles"
                  :key="r.id"
                  class="flag"
                  style="margin-right: 4px;"
                >
                  {{ r.name }}
                </span>
              </td>
              <td>{{ u.timezone }}</td>
              <td>
                <span :class="['flag', u.is_active ? 'success' : 'flag-danger']">
                  {{ u.is_active ? 'Активен' : 'Отключен' }}
                </span>
              </td>
              <td>
                <button
                  type="button"
                  class="secondary"
                  style="padding: 6px 10px; font-size: 13px;"
                  :data-test="`btn-edit-user-${u.id}`"
                  @click="startEditUser(u)"
                >
                  Изменить
                </button>
              </td>
            </tr>
            <tr v-if="filteredUsers.length === 0">
              <td colspan="10" class="muted" style="text-align: center; padding: 20px;">
                Пользователи не найдены
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ===================================================================== -->
    <!-- 2. SCHEDULES & PLANNING TAB -->
    <!-- ===================================================================== -->
    <section v-if="currentTab === 'schedules'" class="panel admin-section" data-test="section-schedules">
      <div class="manager-toggle sub-tabs">
        <button
          type="button"
          :class="{ selected: currentScheduleSubTab === 'schedules' }"
          data-test="subtab-schedules"
          @click="currentScheduleSubTab = 'schedules'"
        >
          Графики сотрудников
        </button>
        <button
          type="button"
          :class="{ selected: currentScheduleSubTab === 'absences' }"
          data-test="subtab-absences"
          @click="currentScheduleSubTab = 'absences'"
        >
          Отсутствия
        </button>
        <button
          type="button"
          :class="{ selected: currentScheduleSubTab === 'calendar' }"
          data-test="subtab-calendar"
          @click="currentScheduleSubTab = 'calendar'"
        >
          Производственный календарь
        </button>
        <button
          type="button"
          :class="{ selected: currentScheduleSubTab === 'distribution' }"
          data-test="subtab-distribution"
          @click="currentScheduleSubTab = 'distribution'"
        >
          Участие в распределении
        </button>
        <button
          type="button"
          :class="{ selected: currentScheduleSubTab === 'planning' }"
          data-test="subtab-planning"
          @click="currentScheduleSubTab = 'planning'"
        >
          Параметры планирования
        </button>
      </div>

      <!-- A. Schedules SubTab -->
      <div v-if="currentScheduleSubTab === 'schedules'" class="subtab-content">
        <ScheduleEditor />
      </div>

      <!-- B. Absences SubTab -->
      <div v-if="currentScheduleSubTab === 'absences'" class="subtab-content">
        <h3>Отсутствия сотрудников</h3>
        <p class="hint" data-test="help-text">Отпуск, больничный или другое отсутствие. Пока оно действует, сотрудник считается недоступным: автоматически карточки ему не назначаются, а в списке выбора L2 у руководителя он отмечается как недоступный. По умолчанию отсутствие задаётся на весь день: с 00:00 до 23:59.</p>

        <!-- Add Absence Form -->
        <div class="panel actions">
          <h4>Зафиксировать новое отсутствие</h4>
          <form class="form" @submit.prevent="handleCreateAbsence">
            <div class="grid">
              <label>
                <span>Сотрудник *</span>
                <select v-model.number="absenceNewUserId" required data-test="select-absence-new-user">
                  <option value="" disabled>-- Выберите сотрудника --</option>
                  <option v-for="u in users" :key="u.id" :value="u.id">
                    {{ u.full_name }} (@{{ u.username }})
                  </option>
                </select>
              </label>
              <label>
                <span>Причина отсутствия</span>
                <input
                  v-model="absenceReason"
                  type="text"
                  placeholder="Отпуск, больничный, отгул..."
                  data-test="input-absence-reason"
                />
              </label>
            </div>

            <div class="grid">
              <label>
                <span>Дата начала</span>
                <input v-model="absenceStartDate" type="date" required data-test="input-absence-start-date" />
              </label>
              <label>
                <span>Время начала</span>
                <select v-model="absenceStartTime" required data-test="input-absence-start-time"><option v-for="t in timeOptions" :key="t" :value="t">{{ t }}</option></select>
              </label>
            </div>

            <div class="grid">
              <label>
                <span>Дата окончания</span>
                <input v-model="absenceEndDate" type="date" required data-test="input-absence-end-date" />
              </label>
              <label>
                <span>Время окончания</span>
                <select v-model="absenceEndTime" required data-test="input-absence-end-time"><option v-for="t in timeOptions" :key="t" :value="t">{{ t }}</option></select>
              </label>
            </div>

            <div>
              <button type="submit" :disabled="busy" data-test="btn-submit-absence">
                Добавить отсутствие
              </button>
            </div>
          </form>
        </div>

        <!-- Filter and Table -->
        <div class="manager-filters" style="margin-top: 20px;">
          <label>
            <span>Фильтр по сотруднику:</span>
            <select v-model.number="absenceUserIdFilter" data-test="select-absence-filter-user" @change="loadAbsences">
              <option value="">Все сотрудники</option>
              <option v-for="u in users" :key="u.id" :value="u.id">
                {{ u.full_name }}
              </option>
            </select>
          </label>
        </div>

        <div class="table-wrap">
          <table class="admin-table" data-test="table-absences">
            <thead>
              <tr>
                <th>ID</th>
                <th>Сотрудник</th>
                <th>Начало</th>
                <th>Окончание</th>
                <th>Причина</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="a in absences" :key="a.id" :data-test="`absence-row-${a.id}`">
                <td>{{ a.id }}</td>
                <td><strong>{{ getUserDisplayName(a.user_id) }}</strong></td>
                <td>{{ formatDateTimeInTz(a.start_at, profileTz) }}</td>
                <td>{{ formatDateTimeInTz(a.end_at, profileTz) }}</td>
                <td>{{ a.reason || '—' }}</td>
                <td>
                  <button
                    type="button"
                    class="danger"
                    style="padding: 5px 10px; font-size: 12px;"
                    :data-test="`btn-delete-absence-${a.id}`"
                    @click="handleDeleteAbsence(a.id)"
                  >
                    Удалить
                  </button>
                </td>
              </tr>
              <tr v-if="absences.length === 0">
                <td colspan="6" class="muted" style="text-align: center; padding: 16px;">
                  Записей об отсутствии не найдено
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- C. Production Calendar SubTab -->
      <div v-if="currentScheduleSubTab === 'calendar'" class="subtab-content">
        <h3>Производственный календарь РФ</h3>
        <p class="hint" data-test="help-text">Отмечайте праздничные и нерабочие дни. В такие даты сотрудники не получают автоматическое назначение, даже если в графике стоит смена, а карточки помечаются «вне рабочего времени». Календарь также подсказывает при составлении графиков: нерабочие дни подсвечены, и шаблон по умолчанию не ставит на них смены. Сам график он не меняет.</p>
        <div class="manager-filters">
          <label>
            <span>Период с:</span>
            <input v-model="calendarFromDate" type="date" data-test="input-calendar-from" />
          </label>
          <label>
            <span>Период по:</span>
            <input v-model="calendarToDate" type="date" data-test="input-calendar-to" />
          </label>
          <button type="button" class="secondary" data-test="btn-load-calendar" @click="loadCalendarDays">
            Показать дни
          </button>
        </div>

        <!-- Edit Calendar Day Modal / Panel -->
        <div v-if="editCalendarDate" class="panel actions" data-test="panel-edit-calendar-day">
          <h4>Редактирование дня: {{ editCalendarDate }}</h4>
          <form class="form" @submit.prevent="handleSaveCalendarDay">
            <div class="grid">
              <label>
                <span>Тип дня</span>
                <select v-model.number="editDayTypeCode" data-test="select-day-type">
                  <option :value="0">0 — Рабочий день</option>
                  <option :value="1">1 — Выходной день</option>
                  <option :value="2">2 — Праздничный / сокращённый</option>
                </select>
              </label>
              <label>
                <span>Комментарий</span>
                <input
                  v-model="editCalendarComment"
                  type="text"
                  placeholder="Причина изменения, праздник..."
                  data-test="input-calendar-comment"
                />
              </label>
            </div>
            <div class="action-row">
              <button type="submit" :disabled="busy" data-test="btn-save-calendar-day">Сохранить</button>
              <button type="button" class="secondary" @click="editCalendarDate = ''">Отмена</button>
            </div>
          </form>
        </div>

        <div class="table-wrap">
          <table class="admin-table" data-test="table-calendar">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Тип дня</th>
                <th>Ручное изменение</th>
                <th>Комментарий</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in calendarDays" :key="d.date" :data-test="`calendar-day-${d.date}`">
                <td><strong>{{ formatDateInTz(d.date, profileTz) }}</strong> ({{ d.date }})</td>
                <td>
                  <span
                    :class="[
                      'flag',
                      d.day_type_code === 0 ? 'success' : d.day_type_code === 1 ? 'status-rejected' : 'status-confirmed',
                    ]"
                  >
                    {{ dayTypeNames[d.day_type_code] || `Тип ${d.day_type_code}` }}
                  </span>
                </td>
                <td>{{ d.is_manual_override ? 'Да' : 'Авто' }}</td>
                <td>{{ d.comment || '—' }}</td>
                <td>
                  <button
                    type="button"
                    class="secondary"
                    style="padding: 5px 10px; font-size: 12px;"
                    :data-test="`btn-edit-calendar-${d.date}`"
                    @click="startEditCalendarDay(d)"
                  >
                    Изменить
                  </button>
                </td>
              </tr>
              <tr v-if="calendarDays.length === 0">
                <td colspan="5" class="muted" style="text-align: center; padding: 16px;">
                  Выберите диапазон дат и нажмите «Показать дни»
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- D. Distribution Membership SubTab -->
      <div v-if="currentScheduleSubTab === 'distribution'" class="subtab-content">
        <h3>Участие сотрудников в автоматическом распределении</h3>
        <p class="hint" data-test="help-text">Переключатель для случаев, когда у сотрудника есть график, но брать новые карточки он сейчас не может, например из-за другой нагрузки на удалённых подключениях. Выключенный сотрудник не получает автоматические назначения; его график и отсутствия не меняются. Каждое изменение требует основания и сохраняется в журнале.</p>
        <p class="hint">
          Включение и исключение сотрудника из пула распределения производится строго на основании решения руководителя с обязательной фиксацией текстового комментария.
        </p>

        <div class="manager-filters">
          <label>
            <span>Пул распределения:</span>
            <select v-model.number="distributionPoolFilter" data-test="select-dist-pool" @change="loadDistributionMembers">
              <option value="">Все пулы</option>
              <option :value="1">Пул 1 (L1 — сопровождение)</option>
              <option :value="2">Пул 2 (L2 — подключение)</option>
            </select>
          </label>
        </div>

        <!-- Edit Distribution Membership Panel -->
        <div v-if="distEditMember" class="panel actions" data-test="panel-edit-distribution">
          <h4>
            {{ distEditEnabled ? 'Включение' : 'Исключение' }} сотрудника
            {{ getUserDisplayName(distEditMember.user_id) }} (Пул {{ distEditMember.pool_code }})
          </h4>
          <form class="form" @submit.prevent="handleSaveDistribution">
            <label>
              <span>Обязательное основание (решение руководителя) *</span>
              <textarea
                v-model="distEditComment"
                required
                rows="3"
                placeholder="Укажите причину или ссылку на решение руководителя..."
                data-test="textarea-dist-comment"
              ></textarea>
            </label>
            <div class="action-row">
              <button
                type="submit"
                :class="distEditEnabled ? '' : 'danger'"
                :disabled="busy"
                data-test="btn-save-distribution"
              >
                {{ distEditEnabled ? 'Включить в распределение' : 'Исключить из распределения' }}
              </button>
              <button type="button" class="secondary" @click="distEditMember = null">Отмена</button>
            </div>
          </form>
        </div>

        <div class="table-wrap">
          <table class="admin-table" data-test="table-distribution">
            <thead>
              <tr>
                <th>Пул</th>
                <th>Сотрудник</th>
                <th>Участие</th>
                <th>Основание</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in distributionMembers" :key="`${m.pool_code}-${m.user_id}`" :data-test="`dist-row-${m.pool_code}-${m.user_id}`">
                <td><strong>{{ m.pool_code === 1 ? 'L1 (Сопровождение)' : 'L2 (Подключение)' }}</strong></td>
                <td>{{ getUserDisplayName(m.user_id) }}</td>
                <td>
                  <span :class="['flag', m.is_enabled ? 'success' : 'flag-danger']">
                    {{ m.is_enabled ? 'Включён' : 'Исключён' }}
                  </span>
                </td>
                <td>{{ m.comment || '—' }}</td>
                <td>
                  <button
                    type="button"
                    :class="m.is_enabled ? 'danger' : 'secondary'"
                    style="padding: 5px 10px; font-size: 12px;"
                    :data-test="`btn-toggle-dist-${m.pool_code}-${m.user_id}`"
                    @click="startEditDistribution(m)"
                  >
                    {{ m.is_enabled ? 'Исключить' : 'Включить' }}
                  </button>
                </td>
              </tr>
              <tr v-if="distributionMembers.length === 0">
                <td colspan="5" class="muted" style="text-align: center; padding: 16px;">
                  Записи распределения не найдены
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- E. Planning Settings SubTab -->
      <div v-if="currentScheduleSubTab === 'planning'" class="subtab-content">
        <h3>Параметры планирования подключений (ADM-010)</h3>
        <p class="hint" data-test="help-text">Эти значения действуют во всех формах создания и переноса карточек (L1, L2, руководитель и клиентский фрейм) и подставляются в выбор даты и времени. «Минимальное время до подключения» — не раньше чем через столько минут от текущего момента; «Горизонт» — насколько дней вперёд можно планировать; длительность — значение по умолчанию, минимум и максимум. Срочные подключения не ограничены минимальным временем, ретроспективные ограничены прошлым.</p>
        <form class="form panel actions" @submit.prevent="handleSavePlanningSettings">
          <div class="grid">
            <label>
              <span>Минимальное время до подключения (мин)</span>
              <input
                v-model.number="planningSettings.min_lead_minutes"
                type="number"
                min="1"
                required
                data-test="input-min-lead"
              />
              <small class="muted">По умолчанию 120 минут (за 2 часа)</small>
            </label>
            <label>
              <span>Горизонт планирования (дней)</span>
              <input
                v-model.number="planningSettings.horizon_days"
                type="number"
                min="1"
                required
                data-test="input-horizon-days"
              />
              <small class="muted">По умолчанию 14 дней</small>
            </label>
          </div>

          <div class="grid">
            <label>
              <span>Длительность по умолчанию (мин)</span>
              <input
                v-model.number="planningSettings.default_duration_minutes"
                type="number"
                min="1"
                required
                data-test="input-default-duration"
              />
            </label>
            <label>
              <span>Минимальная длительность (мин)</span>
              <input
                v-model.number="planningSettings.min_duration_minutes"
                type="number"
                min="1"
                required
                data-test="input-min-duration"
              />
            </label>
          </div>

          <div class="grid">
            <label>
              <span>Максимальная длительность (мин)</span>
              <input
                v-model.number="planningSettings.max_duration_minutes"
                type="number"
                min="1"
                required
                data-test="input-max-duration"
              />
            </label>
          </div>

          <div style="margin-top: 16px;">
            <button type="submit" :disabled="busy" data-test="btn-save-planning">
              Сохранить параметры планирования
            </button>
          </div>
        </form>
      </div>
    </section>

    <!-- ===================================================================== -->
    <!-- 3. SETTINGS & INTEGRATIONS TAB -->
    <!-- ===================================================================== -->
    <section v-if="currentTab === 'settings'" class="panel admin-section" data-test="section-settings">
      <div class="manager-toggle sub-tabs">
        <button
          type="button"
          :class="{ selected: currentSettingsSubTab === 'session' }"
          data-test="subtab-session"
          @click="currentSettingsSubTab = 'session'"
        >
          Автопродление и сообщения
        </button>
        <button
          type="button"
          :class="{ selected: currentSettingsSubTab === 'templates' }"
          data-test="subtab-templates"
          @click="currentSettingsSubTab = 'templates'"
        >
          Шаблоны уведомлений
        </button>
        <button
          type="button"
          :class="{ selected: currentSettingsSubTab === 'integrations' }"
          data-test="subtab-integrations"
          @click="currentSettingsSubTab = 'integrations'"
        >
          Статус интеграций
        </button>
      </div>

      <!-- A. Session Extension & Public Warnings -->
      <div v-if="currentSettingsSubTab === 'session'" class="subtab-content">
        <h3>Интервал автоматического продления и публичные сообщения</h3>
        <p class="hint" data-test="help-text">Если карточка в статусе «Выполняется» дошла до планового окончания, а инженер её ещё не завершил, система сама продлевает её на этот интервал. Когда общая длительность превысит 720 минут, карточка переходит в ожидание результата. Если продление пересекается с другой карточкой инженера, она помечается флагом коллизии.</p>

        <div class="panel actions">
          <h4>Автопродление выполняющейся сессии</h4>
          <form class="form" @submit.prevent="handleSaveExtensionInterval">
            <label>
              <span>Интервал автопродления (в минутах)</span>
              <input
                v-model.number="extensionMinutes"
                type="number"
                min="1"
                max="1440"
                required
                data-test="input-extension-minutes"
              />
              <small class="muted">По умолчанию 15 минут (900 секунд)</small>
            </label>
            <div>
              <button type="submit" :disabled="busy" data-test="btn-save-extension">
                Сохранить интервал
              </button>
            </div>
          </form>
        </div>

        <div class="panel actions" style="margin-top: 16px;">
          <h4>15-минутное публичное предупреждение клиента перед подключением</h4>
          <form class="form" @submit.prevent="handleSavePublicWarning">
            <label class="choice">
              <input
                v-model="publicWarnSettings.enabled"
                type="checkbox"
                data-test="checkbox-pubwarn-enabled"
              />
              <span>Включить отправку предупреждения за 15 минут</span>
            </label>
            <label>
              <span>Шаблон публичного сообщения</span>
              <textarea
                v-model="publicWarnSettings.template"
                rows="3"
                required
                data-test="textarea-pubwarn-template"
              ></textarea>
            </label>
            <div>
              <button type="submit" :disabled="busy" data-test="btn-save-pubwarn">
                Сохранить настройку предупреждения
              </button>
            </div>
          </form>
        </div>

        <div class="panel actions" style="margin-top: 16px;">
          <h4>Публичное сообщение клиенту при отмене карточки</h4>
          <form class="form" @submit.prevent="handleSaveCancellationWarning">
            <label class="choice">
              <input
                v-model="cancelNotificationSettings.enabled"
                type="checkbox"
                data-test="checkbox-cancel-enabled"
              />
              <span>Включить публичное сообщение при отмене</span>
            </label>
            <label>
              <span>Шаблон сообщения об отмене</span>
              <textarea
                v-model="cancelNotificationSettings.template"
                rows="3"
                required
                data-test="textarea-cancel-template"
              ></textarea>
            </label>
            <div>
              <button type="submit" :disabled="busy" data-test="btn-save-cancel-notif">
                Сохранить настройку сообщения об отмене
              </button>
            </div>
          </form>
        </div>
      </div>

      <!-- C. Notification Templates -->
      <div v-if="currentSettingsSubTab === 'templates'" class="subtab-content">
        <h3>Шаблоны уведомлений</h3>
        <p class="hint" data-test="help-text">Код шаблона состоит из события и канала, например «l1_followup.telegram». Выберите шаблон, измените текст и сохраните: следующее уведомление будет отправлено уже по новому тексту. В тексте можно использовать подстановки в фигурных скобках: {card_number} — номер карточки, {ticket} — номер тикета Omnidesk, {timestamp} — плановое время, {duration} — длительность в минутах, {client_suffix} — имя клиента (если известно), {url} — ссылка на карточку, {status} — статус, {reason} — причина, {action} — действие. Неизвестные подстановки система не сохранит. Если шаблон отключён или повреждён, отправляется стандартный текст.</p>

        <!-- Edit Template Modal / Panel -->
        <div v-if="editingTemplate" class="panel actions" data-test="panel-edit-template">
          <h4>Редактирование шаблона: {{ editingTemplate.code }}</h4>
          <form class="form" @submit.prevent="handleSaveTemplate">
            <label>
              <span>Тема (для email/тикета)</span>
              <input v-model="editTemplateSubject" type="text" data-test="input-tpl-subject" />
            </label>
            <label>
              <span>Тело шаблона *</span>
              <textarea
                v-model="editTemplateBody"
                rows="4"
                required
                data-test="textarea-tpl-body"
              ></textarea>
            </label>
            <label class="choice">
              <input v-model="editTemplateVisible" type="checkbox" data-test="checkbox-tpl-visible" />
              <span>Шаблон активен / видим</span>
            </label>
            <div class="action-row">
              <button type="submit" :disabled="busy" data-test="btn-save-template">Сохранить</button>
              <button type="button" class="secondary" @click="editingTemplate = null">Отмена</button>
            </div>
          </form>
        </div>

        <div class="table-wrap">
          <table class="admin-table" data-test="table-templates">
            <thead>
              <tr>
                <th>Код</th>
                <th>Канал</th>
                <th>Тема</th>
                <th>Текст шаблона</th>
                <th>Видимость</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in notificationTemplates" :key="t.code" :data-test="`template-row-${t.code}`">
                <td><strong>{{ t.code }}</strong></td>
                <td>{{ t.channel_code }}</td>
                <td>{{ t.subject_template || '—' }}</td>
                <td><pre class="preserve" style="font-size: 12px;">{{ t.body_template }}</pre></td>
                <td>
                  <span :class="['flag', t.visible ? 'success' : 'flag-danger']">
                    {{ t.visible ? 'Да' : 'Нет' }}
                  </span>
                </td>
                <td>
                  <button
                    type="button"
                    class="secondary"
                    style="padding: 5px 10px; font-size: 12px;"
                    :data-test="`btn-edit-tpl-${t.code}`"
                    @click="startEditTemplate(t)"
                  >
                    Изменить
                  </button>
                </td>
              </tr>
              <tr v-if="notificationTemplates.length === 0">
                <td colspan="6" class="muted" style="text-align: center; padding: 16px;">
                  Шаблоны не найдены
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- D. Integrations Status (Read-Only) -->
      <div v-if="currentSettingsSubTab === 'integrations'" class="subtab-content" data-test="section-integrations">
        <h3>Статус внешних интеграций (Только чтение)</h3>
        <p class="hint" data-test="help-text">Показывает, подключены ли Omnidesk, Telegram и Битрикс24 и включена ли отправка во внешние системы. Это только состояние: настройки и ключи хранятся в окружении сервера и здесь не меняются. Если отправка выключена, уведомления накапливаются, но во внешние системы не уходят.</p>
        <p class="hint">
          Секретные ключи, токены и URL вебхуков хранятся исключительно в конфигурационном файле окружения (.env) и никогда не передаются и не отображаются в интерфейсе.
        </p>

        <div v-if="integrationsStatus" class="grid" style="margin-top: 16px;">
          <!-- Omnidesk Card -->
          <div class="panel" data-test="integration-omnidesk">
            <h4>Omnidesk</h4>
            <dl>
              <dt>Статус настройки:</dt>
              <dd>
                <span :class="['flag', integrationsStatus.omnidesk?.configured ? 'success' : 'flag-danger']">
                  {{ integrationsStatus.omnidesk?.configured ? 'Настроено' : 'Не настроено' }}
                </span>
              </dd>
              <dt>Включено:</dt>
              <dd>
                <span :class="['flag', integrationsStatus.omnidesk?.enabled ? 'success' : 'secondary']">
                  {{ integrationsStatus.omnidesk?.enabled ? 'Включено' : 'Выключено' }}
                </span>
              </dd>
              <dt v-if="integrationsStatus.omnidesk?.domain">Домен:</dt>
              <dd v-if="integrationsStatus.omnidesk?.domain">{{ integrationsStatus.omnidesk.domain }}</dd>
            </dl>
          </div>

          <!-- Telegram Card -->
          <div class="panel" data-test="integration-telegram">
            <h4>Telegram Bot</h4>
            <dl>
              <dt>Статус настройки:</dt>
              <dd>
                <span :class="['flag', integrationsStatus.telegram?.configured ? 'success' : 'flag-danger']">
                  {{ integrationsStatus.telegram?.configured ? 'Настроено' : 'Не настроено' }}
                </span>
              </dd>
              <dt>Включено:</dt>
              <dd>
                <span :class="['flag', integrationsStatus.telegram?.enabled ? 'success' : 'secondary']">
                  {{ integrationsStatus.telegram?.enabled ? 'Включено' : 'Выключено' }}
                </span>
              </dd>
              <dt v-if="integrationsStatus.telegram?.bot_username">Имя бота:</dt>
              <dd v-if="integrationsStatus.telegram?.bot_username">@{{ integrationsStatus.telegram.bot_username }}</dd>
            </dl>
          </div>

          <!-- Bitrix24 Card -->
          <div class="panel" data-test="integration-bitrix24">
            <h4>Битрикс24 Bot</h4>
            <dl>
              <dt>Статус настройки:</dt>
              <dd>
                <span :class="['flag', integrationsStatus.bitrix24?.configured ? 'success' : 'flag-danger']">
                  {{ integrationsStatus.bitrix24?.configured ? 'Настроено' : 'Не настроено' }}
                </span>
              </dd>
              <dt>Включено:</dt>
              <dd>
                <span :class="['flag', integrationsStatus.bitrix24?.enabled ? 'success' : 'secondary']">
                  {{ integrationsStatus.bitrix24?.enabled ? 'Включено' : 'Выключено' }}
                </span>
              </dd>
              <dt v-if="integrationsStatus.bitrix24?.domain">Домен:</dt>
              <dd v-if="integrationsStatus.bitrix24?.domain">{{ integrationsStatus.bitrix24.domain }}</dd>
            </dl>
          </div>
        </div>

        <div v-else class="muted" style="padding: 20px;">
          Загрузка статуса интеграций...
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.admin-workspace {
  width: min(100%, 1400px);
  margin: 0 auto;
}

.admin-tabs {
  margin: 20px 0 24px;
}

.sub-tabs {
  margin: 0 0 20px;
}

.admin-section {
  padding: 24px;
}

.subtab-content {
  display: grid;
  gap: 20px;
}

.table-wrap {
  overflow-x: auto;
  margin-top: 16px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #fff;
}

.admin-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  text-align: left;
}

.admin-table th,
.admin-table td {
  padding: 12px 14px;
  border-bottom: 1px solid #e2e8f0;
  vertical-align: middle;
}

.admin-table th {
  background: #f8fafc;
  color: #475569;
  font-weight: 600;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.admin-table tbody tr:hover {
  background: #f8fbff;
}

.schedule-row-box {
  margin-top: 12px;
  background: #fff;
}

.create-user-box,
.edit-user-box {
  margin-bottom: 24px;
}
</style>
