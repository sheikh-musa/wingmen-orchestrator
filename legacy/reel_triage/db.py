import contextlib

import psycopg
from psycopg.rows import dict_row

from legacy.reel_triage import config


@contextlib.contextmanager
def connect():
    """Autocommit connection to the reel_inbox project (tscuymavysscrvoberrr)."""
    with psycopg.connect(config.reel_inbox_dsn(), row_factory=dict_row,
                         autocommit=True) as conn:
        yield conn
