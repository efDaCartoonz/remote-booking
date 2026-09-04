from collections.abc import Generator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.core.config import settings


@contextmanager
def db_connection() -> Generator[psycopg.Connection, None, None]:
    with psycopg.connect(
        settings.psycopg_database_url, row_factory=dict_row
    ) as connection:
        yield connection


def get_db() -> Generator[psycopg.Connection, None, None]:
    with db_connection() as connection:
        yield connection
