from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import DecisionOutput, TelemetryIn


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_telemetry_json(telemetry: TelemetryIn | dict[str, object]) -> str:
    normalized = TelemetryIn.model_validate(telemetry)
    canonical_payload = normalized.model_dump(mode="json")
    # A server-generated capture time is not part of the client's retry payload.
    if "captured_at" not in normalized.model_fields_set:
        canonical_payload.pop("captured_at", None)
    return json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def telemetry_input_sha256(telemetry: TelemetryIn | dict[str, object]) -> str:
    canonical_json = canonical_telemetry_json(telemetry)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StoredEvent:
    event_id: str
    message_id: str
    received_at: datetime
    input_sha256: str
    telemetry: TelemetryIn
    result: DecisionOutput
    duplicate: bool = False


class MessageIdConflictError(RuntimeError):
    pass


class EventSupersededError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS telemetry (
                    message_id TEXT PRIMARY KEY,
                    site_id TEXT NOT NULL,
                    vehicle_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    captured_at_provided INTEGER CHECK (captured_at_provided IN (0, 1)),
                    received_at TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS decisions (
                    event_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL UNIQUE,
                    decision_code TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    latest_safe_start_min REAL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (message_id) REFERENCES telemetry(message_id)
                );

                CREATE TABLE IF NOT EXISTS authorizations (
                    authorization_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    token_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT,
                    FOREIGN KEY (event_id) REFERENCES decisions(event_id)
                );

                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    authorization_id TEXT NOT NULL,
                    command_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (event_id) REFERENCES decisions(event_id),
                    FOREIGN KEY (authorization_id) REFERENCES authorizations(authorization_id)
                );

                CREATE INDEX IF NOT EXISTS idx_telemetry_site_vehicle_received
                    ON telemetry(site_id, vehicle_id, received_at DESC);
                CREATE INDEX IF NOT EXISTS idx_authorizations_event
                    ON authorizations(event_id, expires_at DESC);
                """
            )
            telemetry_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(telemetry)").fetchall()
            }
            if "captured_at_provided" not in telemetry_columns:
                connection.execute(
                    """
                    ALTER TABLE telemetry
                    ADD COLUMN captured_at_provided INTEGER
                    CHECK (captured_at_provided IN (0, 1))
                    """
                )

    def health(self) -> bool:
        with self._connection() as connection:
            row = connection.execute("SELECT 1 AS ok").fetchone()
        return bool(row and row["ok"] == 1)

    def has_message_id(self, message_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM telemetry WHERE message_id = ? LIMIT 1",
                (message_id,),
            ).fetchone()
        return row is not None

    def authorization_is_valid(
        self,
        *,
        event_id: str,
        token_sha256: str,
        now: datetime,
    ) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM authorizations
                WHERE event_id = ? AND token_sha256 = ?
                  AND used_at IS NULL AND expires_at > ?
                LIMIT 1
                """,
                (event_id, token_sha256, now.isoformat()),
            ).fetchone()
        return row is not None

    @staticmethod
    def _row_to_event(row: sqlite3.Row, *, duplicate: bool = False) -> StoredEvent:
        return StoredEvent(
            event_id=row["event_id"],
            message_id=row["message_id"],
            received_at=datetime.fromisoformat(row["received_at"]),
            input_sha256=row["input_sha256"],
            telemetry=TelemetryIn.model_validate_json(row["payload_json"]),
            result=DecisionOutput.model_validate_json(row["result_json"]),
            duplicate=duplicate,
        )

    @staticmethod
    def _event_select(where_clause: str) -> str:
        return f"""
            SELECT d.event_id, t.message_id, t.received_at, t.input_sha256,
                   t.captured_at_provided, t.payload_json, d.result_json
            FROM decisions d
            JOIN telemetry t ON t.message_id = d.message_id
            WHERE {where_clause}
        """

    @staticmethod
    def _latest_event_id_for_scope(
        connection: sqlite3.Connection,
        event_id: str,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT latest_decision.event_id
            FROM decisions target_decision
            JOIN telemetry target
              ON target.message_id = target_decision.message_id
            JOIN telemetry latest
              ON latest.site_id = target.site_id
             AND latest.vehicle_id = target.vehicle_id
            JOIN decisions latest_decision
              ON latest_decision.message_id = latest.message_id
            WHERE target_decision.event_id = ?
            ORDER BY latest.received_at DESC, latest.rowid DESC
            LIMIT 1
            """,
            (event_id,),
        ).fetchone()
        return str(row["event_id"]) if row else None

    @staticmethod
    def _matches_retry_without_capture(
        row: sqlite3.Row,
        telemetry: TelemetryIn,
    ) -> bool:
        if (
            row["captured_at_provided"] == 1
            or "captured_at" in telemetry.model_fields_set
        ):
            return False
        stored_payload = TelemetryIn.model_validate_json(row["payload_json"]).model_dump(
            mode="json"
        )
        incoming_payload = telemetry.model_dump(mode="json")
        if "captured_at" not in telemetry.model_fields_set:
            stored_payload.pop("captured_at", None)
            incoming_payload.pop("captured_at", None)
        return stored_payload == incoming_payload

    def save_telemetry_and_decision(
        self,
        telemetry: TelemetryIn,
        result: DecisionOutput,
    ) -> StoredEvent:
        payload_json = telemetry.model_dump_json()
        input_sha256 = telemetry_input_sha256(telemetry)
        result_json = result.model_dump_json()
        received_at = _utc_now()
        event_id = f"evt_{uuid4().hex}"

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                self._event_select("t.message_id = ?"),
                (telemetry.message_id,),
            ).fetchone()
            if existing:
                if existing["input_sha256"] == input_sha256:
                    connection.rollback()
                    return self._row_to_event(existing, duplicate=True)
                if not self._matches_retry_without_capture(existing, telemetry):
                    connection.rollback()
                    raise MessageIdConflictError(
                        "message_id already exists with a different telemetry payload"
                    )
                connection.execute(
                    """
                    UPDATE telemetry
                    SET captured_at_provided = 0
                    WHERE message_id = ? AND captured_at_provided IS NULL
                    """,
                    (telemetry.message_id,),
                )
                connection.commit()
                return self._row_to_event(existing, duplicate=True)

            connection.execute(
                """
                INSERT INTO telemetry (
                    message_id, site_id, vehicle_id, source_id, captured_at,
                    captured_at_provided, received_at, input_sha256, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    telemetry.message_id,
                    telemetry.site_id,
                    telemetry.vehicle_id,
                    telemetry.source_id,
                    telemetry.captured_at.isoformat(),
                    int("captured_at" in telemetry.model_fields_set),
                    received_at.isoformat(),
                    input_sha256,
                    payload_json,
                ),
            )
            connection.execute(
                """
                INSERT INTO decisions (
                    event_id, message_id, decision_code, risk_level, permission,
                    latest_safe_start_min, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    telemetry.message_id,
                    result.decision.value,
                    result.risk_level.value,
                    result.permission.value,
                    result.timing.latest_safe_start_min,
                    result_json,
                    received_at.isoformat(),
                ),
            )
            connection.commit()

        return StoredEvent(
            event_id=event_id,
            message_id=telemetry.message_id,
            received_at=received_at,
            input_sha256=input_sha256,
            telemetry=telemetry,
            result=result,
        )

    def get_event(self, event_id: str) -> StoredEvent | None:
        with self._connection() as connection:
            row = connection.execute(
                self._event_select("d.event_id = ?"),
                (event_id,),
            ).fetchone()
        return self._row_to_event(row) if row else None

    def get_latest(self, site_id: str, vehicle_id: str) -> StoredEvent | None:
        sql = (
            self._event_select("t.site_id = ? AND t.vehicle_id = ?")
            + " ORDER BY t.received_at DESC, t.rowid DESC LIMIT 1"
        )
        with self._connection() as connection:
            row = connection.execute(sql, (site_id, vehicle_id)).fetchone()
        return self._row_to_event(row) if row else None

    def list_events(self, site_id: str, vehicle_id: str, limit: int = 20) -> list[StoredEvent]:
        sql = self._event_select("t.site_id = ? AND t.vehicle_id = ?") + " ORDER BY t.received_at DESC LIMIT ?"
        with self._connection() as connection:
            rows = connection.execute(sql, (site_id, vehicle_id, limit)).fetchall()
        return [self._row_to_event(row) for row in rows]

    def create_authorization(
        self,
        *,
        event_id: str,
        owner_id: str,
        token_sha256: str,
        expires_at: datetime,
    ) -> str:
        authorization_id = f"auth_{uuid4().hex}"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest_event_id = self._latest_event_id_for_scope(connection, event_id)
            if latest_event_id != event_id:
                connection.rollback()
                raise EventSupersededError(
                    "Event has been superseded by newer vehicle telemetry"
                )
            connection.execute(
                """
                INSERT INTO authorizations (
                    authorization_id, event_id, owner_id, token_sha256,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    authorization_id,
                    event_id,
                    owner_id,
                    token_sha256,
                    _utc_now().isoformat(),
                    expires_at.isoformat(),
                ),
            )
            connection.commit()
        return authorization_id

    def record_authorized_command(
        self,
        *,
        event_id: str,
        token_sha256: str,
        now: datetime,
        status: str,
        response: dict[str, object],
    ) -> str | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest_event_id = self._latest_event_id_for_scope(connection, event_id)
            if latest_event_id != event_id:
                connection.rollback()
                raise EventSupersededError(
                    "Event has been superseded by newer vehicle telemetry"
                )
            row = connection.execute(
                """
                SELECT authorization_id
                FROM authorizations
                WHERE event_id = ? AND token_sha256 = ?
                  AND used_at IS NULL AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (event_id, token_sha256, now.isoformat()),
            ).fetchone()
            if not row:
                connection.rollback()
                return None
            updated = connection.execute(
                """
                UPDATE authorizations
                SET used_at = ?
                WHERE authorization_id = ? AND used_at IS NULL
                """,
                (now.isoformat(), row["authorization_id"]),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            command_id = f"cmd_{uuid4().hex}"
            connection.execute(
                """
                INSERT INTO commands (
                    command_id, event_id, authorization_id, command_type,
                    status, response_json, created_at
                ) VALUES (?, ?, ?, 'MIGRATE_TO_HIGH_POINT', ?, ?, ?)
                """,
                (
                    command_id,
                    event_id,
                    row["authorization_id"],
                    status,
                    json.dumps(response, ensure_ascii=False, sort_keys=True),
                    _utc_now().isoformat(),
                ),
            )
            connection.commit()
            return command_id
