<script setup lang="ts">
import { computed, ref, watch } from "vue";

/**
 * Date and time picker limited to an allowed window.
 * The date comes from the browser calendar (days outside the window are disabled),
 * the time from separate hour and minute lists; a focused list also accepts typing.
 * Values are wall-clock strings "YYYY-MM-DDTHH:mm" in the profile time zone,
 * the same shape a datetime-local input produces.
 */
const props = withDefaults(
  defineProps<{
    modelValue: string;
    min: string;
    max: string;
    step?: number;
    required?: boolean;
    dataTest?: string;
  }>(),
  { step: 5, required: false, dataTest: "slot" },
);
const emit = defineEmits<{ (event: "update:modelValue", value: string): void }>();

const pad = (value: number) => String(value).padStart(2, "0");

const dateValue = ref(props.modelValue.slice(0, 10));
const hourValue = ref(props.modelValue.slice(11, 13));
const minuteValue = ref(props.modelValue.slice(14, 16));

watch(
  () => props.modelValue,
  (value) => {
    dateValue.value = value.slice(0, 10);
    hourValue.value = value.slice(11, 13);
    minuteValue.value = value.slice(14, 16);
  },
);

const minDate = computed(() => props.min.slice(0, 10));
const maxDate = computed(() => props.max.slice(0, 10));
const minMinutes = computed(() => Number(props.min.slice(11, 13)) * 60 + Number(props.min.slice(14, 16)));
const maxMinutes = computed(() => Number(props.max.slice(11, 13)) * 60 + Number(props.max.slice(14, 16)));

/** Allowed minutes-of-day on a date, as a [from, to] range. */
function rangeFor(date: string): [number, number] {
  let from = 0;
  let to = 24 * 60 - 1;
  if (date === minDate.value) from = minMinutes.value;
  if (date === maxDate.value) to = maxMinutes.value;
  return [from, to];
}

function stepSlots(date: string): number[] {
  const [from, to] = rangeFor(date);
  const slots: number[] = [];
  for (let minutes = Math.ceil(from / props.step) * props.step; minutes <= to; minutes += props.step) slots.push(minutes);
  return slots;
}

const hours = computed(() => {
  const list = Array.from(new Set(stepSlots(dateValue.value).map((minutes) => Math.floor(minutes / 60)))).map(pad);
  if (hourValue.value && !list.includes(hourValue.value)) list.push(hourValue.value);
  return list.sort();
});

const minutes = computed(() => {
  const hour = Number(hourValue.value);
  const list = stepSlots(dateValue.value)
    .filter((slot) => Math.floor(slot / 60) === hour)
    .map((slot) => pad(slot % 60));
  if (minuteValue.value && !list.includes(minuteValue.value)) list.push(minuteValue.value);
  return list.sort();
});

const weekday = computed(() => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(dateValue.value)) return "";
  return new Intl.DateTimeFormat("ru-RU", { weekday: "long", timeZone: "UTC" }).format(new Date(`${dateValue.value}T00:00:00Z`));
});

function commit() {
  emit("update:modelValue", dateValue.value && hourValue.value && minuteValue.value ? `${dateValue.value}T${hourValue.value}:${minuteValue.value}` : "");
}

/** Moves the chosen time to the nearest allowed slot after the date or hour changed. */
function clampTime() {
  if (!dateValue.value || !hourValue.value) return;
  const slots = stepSlots(dateValue.value);
  if (!slots.length) return;
  const current = Number(hourValue.value) * 60 + Number(minuteValue.value || 0);
  const target = slots.find((slot) => slot >= current) ?? slots[slots.length - 1];
  hourValue.value = pad(Math.floor(target / 60));
  minuteValue.value = pad(target % 60);
}

function onDateChange() {
  if (hourValue.value) clampTime();
  commit();
}

function onHourChange() {
  const available = stepSlots(dateValue.value).filter((slot) => Math.floor(slot / 60) === Number(hourValue.value));
  if (!minuteValue.value || (available.length && !available.some((slot) => pad(slot % 60) === minuteValue.value))) {
    minuteValue.value = available.length ? pad(available[0] % 60) : "00";
  }
  commit();
}

function onMinuteChange() {
  commit();
}
</script>

<template>
  <span class="slot-picker" :data-test="dataTest">
    <span class="slot-date">
      <input
        v-model="dateValue"
        type="date"
        :min="minDate"
        :max="maxDate"
        :required="required"
        :data-test="`${dataTest}-date`"
        @change="onDateChange"
      />
      <small class="slot-weekday" :data-test="`${dataTest}-weekday`">{{ weekday }}</small>
    </span>
    <span class="slot-time">
      <select v-model="hourValue" :required="required" :disabled="!dateValue" aria-label="Часы" :data-test="`${dataTest}-hour`" @change="onHourChange">
        <option value="" disabled>чч</option>
        <option v-for="hour in hours" :key="hour" :value="hour">{{ hour }}</option>
      </select>
      <b aria-hidden="true">:</b>
      <select v-model="minuteValue" :required="required" :disabled="!dateValue || !hourValue" aria-label="Минуты" :data-test="`${dataTest}-minute`" @change="onMinuteChange">
        <option value="" disabled>мм</option>
        <option v-for="minute in minutes" :key="minute" :value="minute">{{ minute }}</option>
      </select>
    </span>
  </span>
</template>
