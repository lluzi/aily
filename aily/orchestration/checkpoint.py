from __future__ import annotations

from contextlib import AbstractAsyncContextManager, AbstractContextManager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


def sqlite_checkpointer(db_path: Path | str) -> AbstractContextManager[Any]:
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver.from_conn_string(str(path))


def async_sqlite_checkpointer(db_path: Path | str) -> AbstractAsyncContextManager[Any]:
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return AsyncSqliteSaver.from_conn_string(str(path))
