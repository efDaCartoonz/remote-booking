"""Small safe checks for the isolated reminder-smoke environment."""

import sys

REQUIRED_SERVICES = {"postgres", "redis", "stub", "backend", "worker", "beat"}


def validate_services(services: set[str]) -> None:
    missing = REQUIRED_SERVICES - services
    if missing:
        raise SystemExit(f"missing services: {','.join(sorted(missing))}")


if __name__ == "__main__":
    validate_services(set(sys.argv[1:]))
