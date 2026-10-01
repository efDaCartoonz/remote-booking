<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  getCalendarDays,
  getDayShifts,
  getErrorMessage,
  getScheduleEmployees,
  replaceDayShifts,
  type ScheduleEmployee,
} from "./adminApi";
import {
  addDays,
  copyPeriod,
  enumerateDates,
  generatePattern,
  isoWeekday,
  monthRange,
  quarterHourOptions,
  shiftLabel,
  type PatternKind,
  type ShiftMap,
  type ShiftTimes,
} from "./scheduleGenerator";


const WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const MONTH_NAMES = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
const timeOptions = quarterHourOptions();

const today = new Date();
const year = ref(today.getFullYear());
const month = ref(today.getMonth() + 1);
const busy = ref(false);
const error = ref("");
const success = ref("");

const original = ref<Record<number, ShiftMap>>({});
const draft = ref<Record<number, ShiftMap>>({});
const holidays = ref<Set<string>>(new Set());

const range = computed(() => monthRange(year.value, month.value));
const dates = computed(() => enumerateDates(range.value.from, range.value.to));
const employees = ref<ScheduleEmployee[]>([]);

function roleLabel(user: ScheduleEmployee): string {
  return user.roles.map((r) => (r === 1 ? "L1" : "L2")).join("/");
}

function clone(map: ShiftMap): ShiftMap {
  return Object.fromEntries(Object.entries(map).map(([k, v]) => [k, { ...v }]));
}

function sameMap(a: ShiftMap, b: ShiftMap): boolean {
  const ak = Object.keys(a);
  if (ak.length !== Object.keys(b).length) return false;
  return ak.every((k) => b[k] && a[k].start === b[k].start && a[k].end === b[k].end);
}

const dirtyUserIds = computed(() =>
  employees.value.filter((u) => !sameMap(original.value[u.id] ?? {}, draft.value[u.id] ?? {})).map((u) => u.id),
);

function hhmm(value: string): string {
  return value.slice(0, 5);
}

async function load() {
  busy.value = true;
  error.value = "";
  try {
    const [people, shifts, calendar] = await Promise.all([
      getScheduleEmployees(),
      getDayShifts(range.value.from, range.value.to),
      getCalendarDays({ from_date: range.value.from, to_date: range.value.to }).catch(() => []),
    ]);
    employees.value = people;
    const next: Record<number, ShiftMap> = {};
    for (const user of shifts.users) {
      next[user.user_id] = Object.fromEntries(
        user.days.map((d) => [d.day, { start: hhmm(d.start_time), end: hhmm(d.end_time) }]),
      );
    }
    original.value = next;
    draft.value = Object.fromEntries(Object.entries(next).map(([k, v]) => [Number(k), clone(v)]));
    holidays.value = new Set(calendar.filter((d) => d.day_type_code !== 0).map((d) => d.date));
    selected.value = null;
    generatorUserId.value = null;
  } catch (err) {
    error.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

function shiftMonth(delta: number) {
  const index = year.value * 12 + (month.value - 1) + delta;
  year.value = Math.floor(index / 12);
  month.value = (index % 12) + 1;
}

function dayOf(userId: number): ShiftMap {
  return (draft.value[userId] ??= {});
}

// --- single cell editor ---------------------------------------------------
const selected = ref<{ userId: number; date: string } | null>(null);
const cellStart = ref("09:00");
const cellEnd = ref("18:00");

function selectCell(userId: number, date: string) {
  selected.value = { userId, date };
  generatorUserId.value = null;
  const current = dayOf(userId)[date];
  cellStart.value = current?.start ?? "09:00";
  cellEnd.value = current?.end ?? "18:00";
}

const selectedUser = computed(() => employees.value.find((u) => u.id === selected.value?.userId));

function applyCell() {
  if (!selected.value) return;
  if (cellStart.value >= cellEnd.value) {
    error.value = "Время начала должно быть раньше времени окончания.";
    return;
  }
  error.value = "";
  dayOf(selected.value.userId)[selected.value.date] = { start: cellStart.value, end: cellEnd.value };
}

function clearCell() {
  if (!selected.value) return;
  delete dayOf(selected.value.userId)[selected.value.date];
}

// --- pattern generator ----------------------------------------------------
const generatorUserId = ref<number | null>(null);
const genKind = ref<PatternKind>("weekdays");
const genStart = ref("09:00");
const genEnd = ref("18:00");
const genFrom = ref("");
const genTo = ref("");
const genWeekdays = ref<number[]>([1, 2, 3, 4, 5]);
const genWork = ref(2);
const genRest = ref(2);
const genCycleStart = ref("");
const genSkipHolidays = ref(true);

function openGenerator(userId: number) {
  generatorUserId.value = userId;
  selected.value = null;
  genFrom.value = range.value.from;
  genTo.value = range.value.to;
  genCycleStart.value = range.value.from;
}

function toggleWeekday(day: number) {
  genWeekdays.value = genWeekdays.value.includes(day)
    ? genWeekdays.value.filter((d) => d !== day)
    : [...genWeekdays.value, day].sort();
}

function applyGenerator() {
  if (generatorUserId.value === null) return;
  const from = genFrom.value < range.value.from ? range.value.from : genFrom.value;
  const to = genTo.value > range.value.to ? range.value.to : genTo.value;
  if (!from || !to || from > to) {
    error.value = "Период шаблона должен быть внутри выбранного месяца.";
    return;
  }
  try {
    const generated = generatePattern({
      from,
      to,
      kind: genKind.value,
      times: { start: genStart.value, end: genEnd.value },
      weekdays: genWeekdays.value,
      workDays: genWork.value,
      restDays: genRest.value,
      cycleStart: genCycleStart.value || from,
      skipDates: genSkipHolidays.value ? holidays.value : undefined,
    });
    const map = dayOf(generatorUserId.value);
    for (const date of enumerateDates(from, to)) delete map[date];
    Object.assign(map, generated);
    error.value = "";
  } catch (err) {
    error.value = (err as Error).message === "schedule_start_must_precede_end"
      ? "Время начала должно быть раньше времени окончания."
      : "Не удалось применить шаблон.";
  }
}

async function copyFromPreviousMonth() {
  if (generatorUserId.value === null) return;
  busy.value = true;
  error.value = "";
  try {
    const prevIndex = year.value * 12 + (month.value - 1) - 1;
    const prev = monthRange(Math.floor(prevIndex / 12), (prevIndex % 12) + 1);
    const response = await getDayShifts(prev.from, prev.to);
    const source: ShiftMap = Object.fromEntries(
      (response.users.find((u) => u.user_id === generatorUserId.value)?.days ?? []).map((d) => [
        d.day,
        { start: hhmm(d.start_time), end: hhmm(d.end_time) },
      ]),
    );
    // Keep the weekday rhythm: the first Monday of the previous month maps to the first Monday of this one.
    const shiftBy = (7 - ((isoWeekday(range.value.from) - isoWeekday(prev.from) + 7) % 7)) % 7;
    const length = enumerateDates(range.value.from, range.value.to).length;
    const copied = copyPeriod(source, prev.from, addDays(range.value.from, shiftBy), length - shiftBy, genSkipHolidays.value ? holidays.value : new Set());
    const map = dayOf(generatorUserId.value);
    for (const date of dates.value) delete map[date];
    Object.assign(map, copied);
  } catch (err) {
    error.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

function clearMonth() {
  if (generatorUserId.value === null) return;
  draft.value[generatorUserId.value] = {};
}

// --- saving ----------------------------------------------------------------
async function saveAll() {
  success.value = "";
  error.value = "";
  busy.value = true;
  try {
    for (const userId of dirtyUserIds.value) {
      const user = employees.value.find((u) => u.id === userId);
      const days = Object.entries(draft.value[userId] ?? {})
        .sort(([a], [b]) => (a < b ? -1 : 1))
        .map(([day, t]) => ({ day, start_time: `${t.start}:00`, end_time: `${t.end}:00` }));
      await replaceDayShifts(userId, {
        date_from: range.value.from,
        date_to: range.value.to,
        timezone: user?.timezone || "Asia/Yekaterinburg",
        days,
      });
    }
    success.value = "График сохранён.";
    await load();
  } catch (err) {
    error.value = getErrorMessage(err);
  } finally {
    busy.value = false;
  }
}

function discard() {
  draft.value = Object.fromEntries(Object.entries(original.value).map(([k, v]) => [Number(k), clone(v)]));
  selected.value = null;
}

function cellClass(userId: number, date: string): Record<string, boolean> {
  const current = selected.value;
  return {
    work: !!draft.value[userId]?.[date],
    holiday: holidays.value.has(date),
    chosen: current?.userId === userId && current.date === date,
    weekend: isoWeekday(date) >= 6,
  };
}

function weekdayShort(date: string): string {
  return WEEKDAY_NAMES[isoWeekday(date) - 1];
}

function dayNumber(date: string): number {
  return Number(date.slice(8));
}

const hoursTotal = (userId: number): string => {
  const total = Object.values(draft.value[userId] ?? {}).reduce((sum, t: ShiftTimes) => {
    const [sh, sm] = t.start.split(":").map(Number);
    const [eh, em] = t.end.split(":").map(Number);
    return sum + (eh * 60 + em - (sh * 60 + sm));
  }, 0);
  return `${Math.round((total / 60) * 10) / 10} ч`;
};

watch([year, month], load);
onMounted(load);
</script>

<template>
  <div class="schedule-editor" data-test="schedule-editor">
    <div class="section-heading">
      <h3>Графики работы</h3>
      <div class="action-row">
        <button type="button" class="secondary" data-test="sched-prev" @click="shiftMonth(-1)">←</button>
        <strong data-test="sched-month" style="align-self: center; min-width: 140px; text-align: center;">
          {{ MONTH_NAMES[month - 1] }} {{ year }}
        </strong>
        <button type="button" class="secondary" data-test="sched-next" @click="shiftMonth(1)">→</button>
      </div>
    </div>
    <p class="muted">
      Для каждого сотрудника выберите день и укажите время работы. День без времени — выходной. Любой день можно задать
      независимо от соседних, а «Шаблон» заполняет период сразу: каждый день, по дням недели или по схеме «2/2». Праздники
      из производственного календаря подсвечены и в шаблоне по умолчанию остаются свободными.
    </p>

    <p v-if="error" class="error" role="alert" data-test="sched-error">{{ error }}</p>
    <p v-if="success" class="success" role="status" data-test="sched-success">{{ success }}</p>

    <div class="table-wrap schedule-grid-wrap">
      <table class="schedule-grid" data-test="schedule-grid">
        <thead>
          <tr>
            <th class="sticky-col">Сотрудник</th>
            <th v-for="date in dates" :key="date" :class="{ holiday: holidays.has(date), weekend: isoWeekday(date) >= 6 }">
              <span>{{ dayNumber(date) }}</span>
              <small>{{ weekdayShort(date) }}</small>
            </th>
            <th>Часы</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in employees" :key="user.id" :data-test="`sched-row-${user.id}`">
            <th class="sticky-col">
              <div>
                <strong>{{ user.full_name }}</strong>
                <span class="flag">{{ roleLabel(user) }}</span>
                <span v-if="dirtyUserIds.includes(user.id)" class="flag warning">изменён</span>
              </div>
              <button type="button" class="secondary small" :data-test="`sched-template-${user.id}`" @click="openGenerator(user.id)">
                Шаблон…
              </button>
            </th>
            <td v-for="date in dates" :key="date">
              <button
                type="button"
                class="cell"
                :class="cellClass(user.id, date)"
                :data-test="`cell-${user.id}-${date}`"
                @click="selectCell(user.id, date)"
              >
                {{ shiftLabel(draft[user.id]?.[date]) }}
              </button>
            </td>
            <td class="muted">{{ hoursTotal(user.id) }}</td>
          </tr>
          <tr v-if="employees.length === 0">
            <td :colspan="dates.length + 2" class="muted" style="text-align: center; padding: 16px;">
              Нет активных сотрудников с ролями L1 или L2.
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="selected && selectedUser" class="panel actions" data-test="cell-editor">
      <h4>{{ selectedUser.full_name }} · {{ selected.date }} ({{ weekdayShort(selected.date) }})</h4>
      <div class="grid">
        <label>
          <span>Начало</span>
          <select v-model="cellStart" data-test="cell-start">
            <option v-for="t in timeOptions" :key="t" :value="t">{{ t }}</option>
          </select>
        </label>
        <label>
          <span>Окончание</span>
          <select v-model="cellEnd" data-test="cell-end">
            <option v-for="t in timeOptions" :key="t" :value="t">{{ t }}</option>
          </select>
        </label>
      </div>
      <div class="action-row">
        <button type="button" data-test="cell-apply" @click="applyCell">Задать время</button>
        <button type="button" class="secondary" data-test="cell-off" @click="clearCell">Выходной</button>
        <button type="button" class="secondary" @click="selected = null">Закрыть</button>
      </div>
    </div>

    <div v-if="generatorUserId !== null" class="panel actions" data-test="generator-panel">
      <h4>Шаблон для: {{ employees.find((u) => u.id === generatorUserId)?.full_name }}</h4>
      <div class="grid">
        <label>
          <span>Тип шаблона</span>
          <select v-model="genKind" data-test="gen-kind">
            <option value="weekdays">По дням недели</option>
            <option value="every_day">Каждый день</option>
            <option value="cycle">Работа / выходные через N дней (2/2)</option>
          </select>
        </label>
        <div class="grid">
          <label>
            <span>С</span>
            <select v-model="genStart" data-test="gen-start">
              <option v-for="t in timeOptions" :key="t" :value="t">{{ t }}</option>
            </select>
          </label>
          <label>
            <span>До</span>
            <select v-model="genEnd" data-test="gen-end">
              <option v-for="t in timeOptions" :key="t" :value="t">{{ t }}</option>
            </select>
          </label>
        </div>
      </div>
      <div class="grid">
        <label><span>Период с</span><input v-model="genFrom" type="date" :min="range.from" :max="range.to" data-test="gen-from" /></label>
        <label><span>Период по</span><input v-model="genTo" type="date" :min="range.from" :max="range.to" data-test="gen-to" /></label>
      </div>
      <div v-if="genKind === 'weekdays'" class="manager-toggle" data-test="gen-weekdays">
        <button
          v-for="(name, idx) in WEEKDAY_NAMES"
          :key="name"
          type="button"
          :class="{ selected: genWeekdays.includes(idx + 1) }"
          @click="toggleWeekday(idx + 1)"
        >
          {{ name }}
        </button>
      </div>
      <div v-if="genKind === 'cycle'" class="grid">
        <label><span>Рабочих дней подряд</span><input v-model.number="genWork" type="number" min="1" max="14" data-test="gen-work" /></label>
        <label><span>Выходных подряд</span><input v-model.number="genRest" type="number" min="0" max="14" data-test="gen-rest" /></label>
        <label>
          <span>Первый рабочий день цикла</span>
          <input v-model="genCycleStart" type="date" :min="range.from" :max="range.to" data-test="gen-cycle-start" />
        </label>
      </div>
      <label class="choice">
        <input v-model="genSkipHolidays" type="checkbox" data-test="gen-skip-holidays" />
        <span>Не ставить смены на праздничные и выходные дни из производственного календаря</span>
      </label>
      <div class="action-row">
        <button type="button" data-test="gen-apply" @click="applyGenerator">Применить к периоду</button>
        <button type="button" class="secondary" data-test="gen-copy" :disabled="busy" @click="copyFromPreviousMonth">
          Скопировать прошлый месяц
        </button>
        <button type="button" class="secondary" data-test="gen-clear" @click="clearMonth">Очистить месяц</button>
        <button type="button" class="secondary" @click="generatorUserId = null">Закрыть</button>
      </div>
    </div>

    <div class="action-row">
      <button type="button" data-test="sched-save" :disabled="busy || dirtyUserIds.length === 0" @click="saveAll">
        Сохранить изменения ({{ dirtyUserIds.length }})
      </button>
      <button type="button" class="secondary" data-test="sched-discard" :disabled="busy || dirtyUserIds.length === 0" @click="discard">
        Отменить правки
      </button>
    </div>
  </div>
</template>
