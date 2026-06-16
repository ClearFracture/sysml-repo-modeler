from __future__ import annotations

import threading
from typing import Any, Callable, Protocol


class DocumentStore(Protocol):
    def read(self, name: str, default: dict[str, Any]) -> dict[str, Any]: ...

    def write(self, name: str, payload: dict[str, Any]) -> None: ...

    def mutate(
        self,
        name: str,
        default: dict[str, Any],
        update: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        """Atomically read a document, apply ``update``, persist, and return the new value.

        Implementations must serialize the read-modify-write so concurrent callers
        (the server is threaded) cannot lose each other's changes.
        """
        ...


class ScopedDocumentStore:
    """Namespaces every document name with ``<prefix>/`` against an inner store.

    Used to give each package its own document namespace within a single shared
    Postgres store.
    """

    def __init__(self, inner: "DocumentStore", prefix: str) -> None:
        self._inner = inner
        self._prefix = prefix

    def _scoped(self, name: str) -> str:
        return f"{self._prefix}/{name}"

    def read(self, name: str, default: dict[str, Any]) -> dict[str, Any]:
        return self._inner.read(self._scoped(name), default)

    def write(self, name: str, payload: dict[str, Any]) -> None:
        self._inner.write(self._scoped(name), payload)

    def mutate(
        self,
        name: str,
        default: dict[str, Any],
        update: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        return self._inner.mutate(self._scoped(name), default, update)


class PostgresDocumentStore:
    def __init__(self, database_url: str) -> None:
        try:
            import psycopg
            from psycopg.types.json import Jsonb
        except ImportError as error:
            raise RuntimeError(
                "DATABASE_URL is configured, but psycopg is not installed. "
                "Install the sysml-backend dependencies before using Postgres."
            ) from error

        self.database_url = database_url
        self._psycopg = psycopg
        self._jsonb = Jsonb
        self._lock = threading.RLock()
        self._connection: Any | None = None

    def read(self, name: str, default: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            connection = self._connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    "select payload from sysml_backend_documents where name = %s",
                    (name,),
                )
                row = cursor.fetchone()
            connection.commit()
        if row is None:
            return default
        payload = row[0]
        return payload if isinstance(payload, dict) else default

    def write(self, name: str, payload: dict[str, Any]) -> None:
        with self._lock:
            connection = self._connect()
            self._upsert(connection, name, payload)
            connection.commit()

    def mutate(
        self,
        name: str,
        default: dict[str, Any],
        update: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            connection = self._connect()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "select payload from sysml_backend_documents where name = %s for update",
                        (name,),
                    )
                    row = cursor.fetchone()
                current = (
                    row[0] if row is not None and isinstance(row[0], dict) else default
                )
                updated = update(current)
                self._upsert(connection, name, updated)
                connection.commit()
                return updated
            except Exception:
                connection.rollback()
                raise

    def _connect(self) -> Any:
        if self._connection is None or self._connection.closed:
            self._connection = self._psycopg.connect(self.database_url)
        return self._connection

    def _upsert(self, connection: Any, name: str, payload: dict[str, Any]) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into sysml_backend_documents (name, payload, updated_at)
                values (%s, %s, now())
                on conflict (name)
                do update set payload = excluded.payload, updated_at = now()
                """,
                (name, self._jsonb(payload)),
            )


def create_document_store(database_url: str | None) -> DocumentStore:
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required. Run Alembic before starting the app."
        )
    return PostgresDocumentStore(database_url)
