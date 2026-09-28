from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

from config import SQLITE_DB_PATH


PathLike = Union[str, Path]


def _normalize_db_path(
    db_path: PathLike = SQLITE_DB_PATH,
) -> str:
    if str(db_path) == ":memory:":
        return ":memory:"

    path = Path(db_path).expanduser()
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    return str(path)


def connect_cache_db(
    db_path: PathLike = SQLITE_DB_PATH,
) -> sqlite3.Connection:
    connection = sqlite3.connect(
        _normalize_db_path(db_path),
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute(
        "PRAGMA foreign_keys = ON"
    )
    connection.execute(
        "PRAGMA busy_timeout = 30000"
    )

    # WAL is useful for the normal file-backed application DB. SQLite may
    # return a different mode for special databases such as :memory:, which is
    # fine for tests.
    try:
        connection.execute(
            "PRAGMA journal_mode = WAL"
        )
    except sqlite3.DatabaseError:
        pass

    return connection


@contextmanager
def cache_connection(
    db_path: PathLike = SQLITE_DB_PATH,
) -> Iterator[sqlite3.Connection]:
    connection = connect_cache_db(
        db_path
    )

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()