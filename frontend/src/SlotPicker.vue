<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { enumerateDates } from "./admin/scheduleGenerator";

/**
 * Date and time picker limited to an allowed window.
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
  { step: 15, required: false, dataTest: "slot" },
);
const emit = defineEmits<{ (event: "update:modelValue", value: string): void }>();

const MAX_DAYS = 400;
const dateValue = ref(props.modelValue.slice(0, 10));
const timeValue = ref(props.modelValue.slice(11, 16));

watch(
  () => props.modelValue,
  (value) => {
    dateValue.value = value.slice(0, 10);
    timeValue.value = value.slice(11, 16);
  },
);

const minDate = computed(() => props.min.slice(0, 10));
const maxDate = computed(() => props.max.slice(0, 10));

const dates = computed(() => {
  const list = enumerateDates(minDate.value, maxDate.value).slice(0, MAX_DAYS);
  if (dateValue.value && !list.includes(dateValue.value)) list.unshift(dateValue.value);
  return list;
});

const formatter = new Intl.DateTimeFormat("ru-RU", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
function dateLabel(date: string): string {
  return formatter.format(new Date(`${date}T00:00:00Z`));
}

function gridTimes(): string[] {
  const list: string[] = [];
  for (let minutes = 0; minutes < 24 * 60; minutes += props.step) {
    list.push(`${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`);
  }
  return list;
}

function allowedTimes(date: string): string[] {
  return gridTimes().filter((time) => {
    if (date === minDate.value && time < props.min.slice(11, 16)) return false;
    if (date === maxDate.value && time > props.max.slice(11, 16)) return false;
    return true;
  });
}

const times = computed(() => {
  const list = allowedTimes(dateValue.value);
  if (timeValue.value && !list.includes(timeValue.value)) list.push(timeValue.value);
  return list.sort();
});

function commit() {
  emit("update:modelValue", dateValue.value && timeValue.value ? `${dateValue.value}T${timeValue.value}` : "");
}

function onDateChange() {
  const allowed = allowedTimes(dateValue.value);
  if (timeValue.value && allowed.length && !allowed.includes(timeValue.value)) {
    timeValue.value = allowed.find((time) => time >= timeValue.value) ?? allowed[allowed.length - 1];
  }
  commit();
}

function onTimeChange() {
  commit();
}
</script>

<template>
  <span class="slot-picker" :data-test="dataTest">
    <select v-model="dateValue" :required="required" :data-test="`${dataTest}-date`" @change="onDateChange">
      <option value="" disabled>Дата</option>
      <option v-for="date in dates" :key="date" :value="date">{{ dateLabel(date) }}</option>
    </select>
    <select v-model="timeValue" :required="required" :disabled="!dateValue" :data-test="`${dataTest}-time`" @change="onTimeChange">
      <option value="" disabled>Время</option>
      <option v-for="time in times" :key="time" :value="time">{{ time }}</option>
    </select>
  </span>
</template>
