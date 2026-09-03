from __future__ import annotations

from enum import IntEnum, StrEnum


class CardStatus(IntEnum):
    CREATED = 0
    ASSIGNED = 1
    CONFIRMED = 2
    IN_PROGRESS = 3
    REJECTED = 4
    COMPLETED = 5
    CANCELLED = 6


class CardStatusSlug(StrEnum):
    CREATED = "created"
    ASSIGNED = "assigned"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AssignmentMethod(IntEnum):
    AUTO = 0
    MANAGER = 1
    L2_SELF = 2
    RETROACTIVE = 3


class AssignmentCycleStatus(IntEnum):
    IN_PROGRESS = 0
    ASSIGNED = 1
    ALL_REJECTED = 2
    CANCELLED = 3


class AssignmentAttemptStatus(IntEnum):
    PENDING = 0
    CONFIRMED = 1
    REJECTED = 2
    SKIPPED = 3


class DistributionPool(IntEnum):
    L1 = 1
    L2 = 2


class RoleId(IntEnum):
    L1 = 1
    L2 = 2
    MANAGER = 3
    ADMIN = 4


class CreatedSource(IntEnum):
    INTERNAL = 0
    FRAME = 1
    SYSTEM = 2
    INTEGRATION = 3


class ActorType(IntEnum):
    INTERNAL_USER = 0
    FRAME_CLIENT = 1
    SYSTEM = 2
    OMNIDESK = 3


class CardEventType(IntEnum):
    CREATED = 0
    STATUS_CHANGED = 1
    ENGINEER_ASSIGNED = 2
    L1_CLIENT_INFORMED = 3
    DETAILS_UPDATED = 4


class AuditAction(IntEnum):
    CREATE = 0
    UPDATE = 1


CARD_STATUS_LABELS: dict[CardStatus, str] = {
    CardStatus.CREATED: "Создано",
    CardStatus.ASSIGNED: "Назначено",
    CardStatus.CONFIRMED: "Подтверждено",
    CardStatus.IN_PROGRESS: "Выполняется",
    CardStatus.REJECTED: "Отклонено",
    CardStatus.COMPLETED: "Завершено",
    CardStatus.CANCELLED: "Отменено",
}

CARD_STATUS_SLUGS: dict[CardStatus, CardStatusSlug] = {
    CardStatus.CREATED: CardStatusSlug.CREATED,
    CardStatus.ASSIGNED: CardStatusSlug.ASSIGNED,
    CardStatus.CONFIRMED: CardStatusSlug.CONFIRMED,
    CardStatus.IN_PROGRESS: CardStatusSlug.IN_PROGRESS,
    CardStatus.REJECTED: CardStatusSlug.REJECTED,
    CardStatus.COMPLETED: CardStatusSlug.COMPLETED,
    CardStatus.CANCELLED: CardStatusSlug.CANCELLED,
}

TERMINAL_STATUSES = frozenset({CardStatus.COMPLETED, CardStatus.CANCELLED})

ALLOWED_STATUS_TRANSITIONS: dict[CardStatus, frozenset[CardStatus]] = {
    CardStatus.CREATED: frozenset({CardStatus.ASSIGNED, CardStatus.REJECTED}),
    CardStatus.ASSIGNED: frozenset(
        {
            CardStatus.ASSIGNED,
            CardStatus.CONFIRMED,
            CardStatus.REJECTED,
            CardStatus.CANCELLED,
            CardStatus.IN_PROGRESS,
        }
    ),
    CardStatus.CONFIRMED: frozenset(
        {CardStatus.ASSIGNED, CardStatus.CANCELLED, CardStatus.IN_PROGRESS}
    ),
    CardStatus.REJECTED: frozenset(
        {CardStatus.CREATED, CardStatus.ASSIGNED, CardStatus.CANCELLED}
    ),
    CardStatus.IN_PROGRESS: frozenset({CardStatus.COMPLETED}),
    CardStatus.COMPLETED: frozenset(),
    CardStatus.CANCELLED: frozenset(),
}


def status_label(status: CardStatus) -> str:
    return CARD_STATUS_LABELS[status]


def status_slug(status: CardStatus) -> CardStatusSlug:
    return CARD_STATUS_SLUGS[status]
