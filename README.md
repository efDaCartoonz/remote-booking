# Remote Booking New / RDM

Remote Desktop Manager — сервис бронирования и управления удалёнными подключениями инженеров технической поддержки.

Текущая стадия: технический каркас MVP.

## Состав

- `backend/` — FastAPI backend, API, фоновые задачи Celery.
- `frontend/` — Vue 3 + Vite frontend для внутреннего интерфейса и клиентского фрейма.
- `Docs/` — проектные документы.
- `docker-compose.yml` — локальный/dev/stage запуск сервисов.

## Быстрый запуск

1. Скопировать `.env.example` в `.env`.
2. Проверить значения переменных.
3. Запустить:

```bash
docker compose up --build
```

Проверки:

- Backend live: `http://localhost:8000/health/live`
- Backend ready: `http://localhost:8000/health/ready`
- Frontend: `http://localhost:8080`

