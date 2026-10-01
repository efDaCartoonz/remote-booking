<script setup lang="ts">
import { computed } from "vue";

type NavItem = { href: string; label: string; roles: number[] };

const props = defineProps<{ roles: number[]; fullName: string; path: string }>();
const emit = defineEmits<{ (event: "logout"): void }>();

const ITEMS: NavItem[] = [
  { href: "/work", label: "Мои карточки", roles: [1, 2] },
  { href: "/manager", label: "Панель руководителя", roles: [3] },
  { href: "/schedules", label: "Графики работы", roles: [3, 4] },
  { href: "/reports", label: "Отчёты", roles: [3, 4] },
  { href: "/admin", label: "Администрирование", roles: [4] },
  { href: "/profile", label: "Профиль", roles: [1, 2, 3, 4] },
];

const items = computed(() => ITEMS.filter((item) => item.roles.some((role) => props.roles.includes(role))));

function isActive(href: string): boolean {
  if (href === "/work") return props.path === "/" || props.path === "/work";
  return props.path === href || props.path.startsWith(`${href}/`);
}
</script>

<template>
  <nav class="sidebar" aria-label="Разделы" data-test="sidebar">
    <div class="sidebar-brand">
      <p class="eyebrow">RDM</p>
    </div>
    <strong class="sidebar-user">{{ fullName }}</strong>
    <ul class="sidebar-list">
      <li v-for="item in items" :key="item.href">
        <a :href="item.href" :class="{ active: isActive(item.href) }" :aria-current="isActive(item.href) ? 'page' : undefined">
          {{ item.label }}
        </a>
      </li>
    </ul>
    <button type="button" class="sidebar-logout" data-test="sidebar-logout" @click="emit('logout')">Выйти</button>
  </nav>
</template>
