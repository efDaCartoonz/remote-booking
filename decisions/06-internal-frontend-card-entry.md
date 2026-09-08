# Решение: внутренний frontend для перехода из уведомления

Дата: 2026-09-08
Исходный HEAD: `817d34f6fd255693c677c4a7becc9c5ba21e753a`

## Решение

- Внутренний UI открывает карточку по маршруту `/cards/{public_id}`.
- Авторизация выполняется через same-origin `POST /api/v1/auth/login`; сессия хранится только в HttpOnly cookie backend.
- При загрузке UI проверяет `GET /api/v1/auth/me`; после успешного входа возвращает пользователя на исходный путь карточки.
- Nginx frontend обслуживает SPA и проксирует `/api/` в сервис `backend`; браузер не знает адрес backend.
- Публичный адрес ссылки уведомления задаётся `NOTIFICATION_CARD_BASE_URL` в `.env`: для RPi stage — адрес frontend в доверенной LAN, для VPS — HTTPS-домен.
- Для доверенной LAN допускается `AUTH_COOKIE_SECURE=false`; на VPS обязательно `AUTH_COOKIE_SECURE=true`.
- Ошибки API `401`, `403`, `404` показываются как отдельные состояния. UI read-only и не добавляет действий карточки.

## Границы

В этот этап не входят scanner, delivery, изменение backend lifecycle, новые уведомления, push и обновление RPi.
