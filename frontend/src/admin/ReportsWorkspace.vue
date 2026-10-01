<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  getErrorMessage,
  getReportsL2Load,
  getReportsOverdue,
  getReportsSummary,
  L2LoadItem,
  OverdueCardItem,
  SummaryReport,
} from "./adminApi";
import {
  addDays,
  calculateReportPeriodUtc,
  formatDateTimeInTz,
  getTodayDateString,
} from "./adminTimezone";

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

const hasReportsAccess = computed(() => {
  if (!props.currentUser || !Array.isArray(props.currentUser.roles)) return false;
  return props.currentUser.roles.some((r) => {
    const roleId = typeof r === "number" ? r : r.id;
    return roleId === 3 || roleId === 4; // Manager or Admin
  });
});

// Period selection state
const today = getTodayDateString(profileTz.value);
const periodStartDate = ref(addDays(today, -30));
const periodEndDate = ref(today);

// Data state
const summary = ref<SummaryReport | null>(null);
const overdueItems = ref<OverdueCardItem[]>([]);
const overdueTotal = ref(0);
const overdueLimit = ref(20);
const overdueOffset = ref(0);
const l2LoadItems = ref<L2LoadItem[]>([]);

// UI state
const busy = ref(false);
const error = ref("");

function setPreset(preset: "today" | "yesterday" | "week" | "month" | "last30") {
  const t = getTodayDateString(profileTz.value);
  if (preset === "today") {
    periodStartDate.value = t;
    periodEndDate.value = t;
  } else if (preset === "yesterday") {
    const y = addDays(t, -1);
    periodStartDate.value = y;
    periodEndDate.value = y;
  } else if (preset === "week") {
    periodStartDate.value = addDays(t, -7);
    periodEndDate.value = t;
  } else if (preset === "month") {
    const [y, m] = t.split("-");
    periodStartDate.value = `${y}-${m}-01`;
    periodEndDate.value = t;
  } else if (preset === "last30") {
    periodStartDate.value = addDays(t, -30);
    periodEndDate.value = t;
  }
  loadReports();
}

async function loadReports() {
  if (!hasReportsAccess.value) return;
  error.value = "";

  if (!periodStartDate.value || !periodEndDate.value) {
    error.value = "Укажите даты начала и окончания периода.";
    return;
  }
  if (periodStartDate.value > periodEndDate.value) {
    error.value = "Дата начала периода должна быть раньше или равна дате окончания.";
    return;
  }

  busy.value = true;
  overdueOffset.value = 0;

  try {
    const { fromIso, toIso } = calculateReportPeriodUtc(
      periodStartDate.value,
      periodEndDate.value,
      profileTz.value
    );

    const [summaryRes, overdueRes, l2Res] = await Promise.all([
      getReportsSummary(fromIso, toIso),
      getReportsOverdue(fromIso, toIso, overdueLimit.value, overdueOffset.value),
      getReportsL2Load(fromIso, toIso),
    ]);

    summary.value = summaryRes;
    overdueItems.value = overdueRes.items;
    overdueTotal.value = overdueRes.total;
    l2LoadItems.value = l2Res.items;
  } catch (err) {
    error.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

async function loadOverduePage(newOffset: number) {
  if (!hasReportsAccess.value || newOffset < 0 || newOffset >= overdueTotal.value) return;
  busy.value = true;
  try {
    const { fromIso, toIso } = calculateReportPeriodUtc(
      periodStartDate.value,
      periodEndDate.value,
      profileTz.value
    );
    const res = await getReportsOverdue(fromIso, toIso, overdueLimit.value, newOffset);
    overdueItems.value = res.items;
    overdueTotal.value = res.total;
    overdueOffset.value = newOffset;
  } catch (err) {
    error.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

function formatShare(share: { numerator: number; denominator: number; value: number | null } | undefined): string {
  if (!share || share.value === null || share.value === undefined) {
    return "нет данных";
  }
  const pct = (share.value * 100).toFixed(1);
  return `${pct}% (${share.numerator} из ${share.denominator})`;
}

function formatMinutes(minutes: number): string {
  if (!minutes) return "0 мин";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h > 0 && m > 0) return `${h} ч ${m} мин`;
  if (h > 0) return `${h} ч`;
  return `${m} мин`;
}

onMounted(() => {
  if (hasReportsAccess.value) {
    loadReports();
  }
});
</script>

<template>
  <div v-if="hasReportsAccess" class="reports-workspace" data-test="reports-workspace">
    <div class="top">
      <div>
        <p class="eyebrow">Отчёты и аналитика RDM</p>
        <h1>Сводные отчёты</h1>
      </div>
    </div>

    <!-- Error Alert -->
    <div v-if="error" class="panel warning error" data-test="reports-error">
      {{ error }}
    </div>

    <!-- Period Selection Panel -->
    <section class="panel filters-panel" data-test="reports-period-selector">
      <div class="period-controls">
        <div class="date-inputs">
          <label>
            <span>Период с (включительно):</span>
            <input
              v-model="periodStartDate"
              type="date"
              data-test="input-report-from"
              @change="loadReports"
            />
          </label>
          <label>
            <span>Период по (включительно):</span>
            <input
              v-model="periodEndDate"
              type="date"
              data-test="input-report-to"
              @change="loadReports"
            />
          </label>
        </div>

        <div class="preset-buttons">
          <button type="button" class="secondary" data-test="preset-today" @click="setPreset('today')">
            Сегодня
          </button>
          <button type="button" class="secondary" data-test="preset-yesterday" @click="setPreset('yesterday')">
            Вчера
          </button>
          <button type="button" class="secondary" data-test="preset-week" @click="setPreset('week')">
            Неделя
          </button>
          <button type="button" class="secondary" data-test="preset-month" @click="setPreset('month')">
            Этот месяц
          </button>
          <button type="button" class="secondary" data-test="preset-last30" @click="setPreset('last30')">
            30 дней
          </button>
          <button type="button" :disabled="busy" data-test="btn-apply-period" @click="loadReports">
            {{ busy ? 'Загрузка...' : 'Применить' }}
          </button>
        </div>
      </div>
      <p class="hint" style="margin: 12px 0 0; font-size: 12px;">
        Период рассчитывается в часовом поясе вашего профиля ({{ profileTz }}) по событиям [from, to).
      </p>
    </section>

    <!-- 1. Summary Metrics -->
    <section v-if="summary" class="summary-section" data-test="section-summary">
      <h2>Ключевые показатели за период</h2>
      <div class="manager-stats">
        <div class="panel" data-test="stat-created">
          <span>Создано заявок</span>
          <strong>{{ summary.created }}</strong>
        </div>
        <div class="panel" data-test="stat-completed">
          <span>Завершено сессий</span>
          <strong>{{ summary.completed }}</strong>
        </div>
        <div class="panel" data-test="stat-rejected-share">
          <span>Доля отклонённых</span>
          <strong>{{ formatShare(summary.rejected_share) }}</strong>
        </div>
        <div class="panel" data-test="stat-repeat-rejected-share">
          <span>Доля повторно отклонённых</span>
          <strong>{{ formatShare(summary.repeat_rejected_share) }}</strong>
        </div>
        <div class="panel" data-test="stat-overdue">
          <span>Просроченные карточки</span>
          <strong>{{ summary.overdue?.count ?? 0 }}</strong>
        </div>
        <div class="panel" data-test="stat-urgent">
          <span>Срочные подключения</span>
          <strong>{{ summary.urgent?.count ?? 0 }}</strong>
        </div>
        <div class="panel" data-test="stat-urgent-collisions">
          <span>Срочные коллизии</span>
          <strong>{{ summary.urgent_collisions?.count ?? 0 }}</strong>
        </div>
      </div>
    </section>

    <!-- 2. Overdue Cards List -->
    <section class="panel report-section" data-test="section-overdue">
      <div class="section-heading">
        <h2>Просроченные карточки за период ({{ overdueTotal }})</h2>
      </div>

      <div class="table-wrap">
        <table class="report-table" data-test="table-overdue">
          <thead>
            <tr>
              <th>Номер</th>
              <th>Статус</th>
              <th>Плановое начало (в вашем часовом поясе)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in overdueItems" :key="item.public_id" :data-test="`overdue-row-${item.number}`">
              <td><strong>{{ item.number }}</strong></td>
              <td>
                <span class="flag flag-danger">{{ item.status_label || item.status }}</span>
              </td>
              <td>{{ formatDateTimeInTz(item.planned_start_at, profileTz) }}</td>
            </tr>
            <tr v-if="overdueItems.length === 0">
              <td colspan="3" class="muted" style="text-align: center; padding: 20px;">
                Просроченных карточек за выбранный период нет
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Pagination -->
      <div v-if="overdueTotal > overdueLimit" class="pagination-controls" data-test="pagination-overdue">
        <button
          type="button"
          class="secondary"
          :disabled="overdueOffset === 0 || busy"
          data-test="btn-prev-overdue"
          @click="loadOverduePage(overdueOffset - overdueLimit)"
        >
          ← Предыдущая
        </button>
        <span class="muted" style="font-size: 13px;">
          Показано {{ overdueOffset + 1 }}–{{ Math.min(overdueOffset + overdueLimit, overdueTotal) }} из {{ overdueTotal }}
        </span>
        <button
          type="button"
          class="secondary"
          :disabled="overdueOffset + overdueLimit >= overdueTotal || busy"
          data-test="btn-next-overdue"
          @click="loadOverduePage(overdueOffset + overdueLimit)"
        >
          Следующая →
        </button>
      </div>
    </section>

    <!-- 3. L2 Engineers Load -->
    <section class="panel report-section" data-test="section-l2-load">
      <div class="section-heading">
        <h2>Загрузка инженеров L2 за период</h2>
      </div>

      <div class="table-wrap">
        <table class="report-table" data-test="table-l2-load">
          <thead>
            <tr>
              <th>Инженер L2</th>
              <th>Назначено карточек</th>
              <th>Завершено подключений</th>
              <th>Запланированное время</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="eng in l2LoadItems" :key="eng.user_id" :data-test="`l2-load-row-${eng.user_id}`">
              <td><strong>{{ eng.full_name }}</strong> (ID {{ eng.user_id }})</td>
              <td>{{ eng.assigned }}</td>
              <td>{{ eng.completed }}</td>
              <td>{{ formatMinutes(eng.planned_minutes) }}</td>
            </tr>
            <tr v-if="l2LoadItems.length === 0">
              <td colspan="4" class="muted" style="text-align: center; padding: 20px;">
                Данные по инженерам L2 отсутствуют
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.reports-workspace {
  width: min(100%, 1400px);
  margin: 0 auto;
}

.filters-panel {
  padding: 20px 24px;
  margin-bottom: 24px;
}

.period-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 16px;
}

.date-inputs {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.preset-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.preset-buttons button {
  padding: 9px 12px;
  font-size: 13px;
}

.summary-section {
  margin-bottom: 24px;
}

.report-section {
  margin-bottom: 24px;
  padding: 24px;
}

.table-wrap {
  overflow-x: auto;
  margin-top: 16px;
  border: 1px solid var(--rdm-border);
  border-radius: 0;
  background: var(--rdm-layer);
}

.report-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
  text-align: left;
}

.report-table th,
.report-table td {
  padding: 12px 16px;
  border-bottom: 1px solid var(--rdm-border);
  vertical-align: middle;
}

.report-table th {
  background: var(--rdm-border);
  color: var(--rdm-text);
  font-weight: 600;
  font-size: 14px;
}

.report-table tbody tr:hover {
  background: var(--rdm-layer-hover);
}

.pagination-controls {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 16px;
}
</style>
