<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

type User = { username: string; full_name: string };
type Card = { id: string; number: string; status_label: string; omnidesk_ticket_number: string; description?: string | null; planned_start_at: string };
const user = ref<User | null>(null), card = ref<Card | null>(null), busy = ref(true), errorStatus = ref<number | null>(null);
const username = ref(""), password = ref(""), loginError = ref(false);
const cardId = computed(() => location.pathname.match(/^\/cards\/([^/]+)\/?$/)?.[1]);
async function api(path: string, init?: RequestInit) { const response = await fetch(path, { credentials: "same-origin", ...init, headers: { "Content-Type": "application/json", ...init?.headers } }); if (!response.ok) throw Object.assign(new Error(), { status: response.status }); return response.status === 204 ? null : response.json(); }
async function load() { busy.value = true; errorStatus.value = null; try { user.value = await api("/api/v1/auth/me"); if (cardId.value) card.value = await api(`/api/v1/cards/${encodeURIComponent(cardId.value)}`); } catch (e) { const status = (e as { status?: number }).status ?? 500; if (status === 401) user.value = null; else errorStatus.value = status; } finally { busy.value = false; } }
async function login() { loginError.value = false; try { await api("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ username: username.value, password: password.value }) }); password.value = ""; await load(); } catch (e) { loginError.value = (e as { status?: number }).status === 401; } }
async function logout() { await api("/api/v1/auth/logout", { method: "POST" }); user.value = null; card.value = null; }
onMounted(load);
</script>

<template>
  <main class="shell"><section class="card">
    <p v-if="busy">Проверяем сессию…</p>
    <template v-else-if="!user"><p class="eyebrow">RDM</p><h1>Вход</h1><form @submit.prevent="login"><label>Логин<input v-model="username" autocomplete="username" required /></label><label>Пароль<input v-model="password" type="password" autocomplete="current-password" required /></label><p v-if="loginError" class="error">Неверный логин или пароль (401).</p><button>Войти</button></form></template>
    <template v-else-if="errorStatus"><h1>Не удалось открыть карточку</h1><p class="error">{{ errorStatus === 403 ? "Доступ запрещён (403)." : errorStatus === 404 ? "Карточка не найдена (404)." : `Ошибка сервера (${errorStatus}).` }}</p><button @click="load">Повторить</button></template>
    <template v-else-if="card"><div class="top"><p class="eyebrow">Карточка RDM</p><button class="secondary" @click="logout">Выйти</button></div><h1>{{ card.number }}</h1><p class="status">{{ card.status_label }}</p><dl><dt>Тикет</dt><dd>{{ card.omnidesk_ticket_number }}</dd><dt>Начало</dt><dd>{{ new Date(card.planned_start_at).toLocaleString() }}</dd><dt>Описание</dt><dd>{{ card.description || "—" }}</dd></dl></template>
    <template v-else><h1>RDM</h1><p>Вы вошли как {{ user.full_name || user.username }}.</p><button class="secondary" @click="logout">Выйти</button></template>
  </section></main>
</template>
