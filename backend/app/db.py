from collections.abc import Generator

import psycopg
from psycopg.rows import dict_row

from app.core.config import settings


def get_db() -> Generator[psycopg.Connection, None, None]:
    with psycopg.connect(settings.psycopg_database_url, row_factory=dict_row) as connection:
        yield connection
