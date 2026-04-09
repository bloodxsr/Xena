from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class GuildConfig:
    guild_id: int
    log_channel_id: int | None = None
    welcome_channel_id: int | None = None
    rules_channel_id: int | None = None
    chat_channel_id: int | None = None
    help_channel_id: int | None = None
    about_channel_id: int | None = None
    perks_channel_id: int | None = None
    admin_role_name: str = "Admin"
    mod_role_name: str = "Moderator"
    sync_mode: str = "global"
    sync_guild_id: int | None = None
    verification_url: str | None = None
    raid_detection_enabled: bool = True
    raid_gate_threshold: float = 0.72
    raid_monitor_window_seconds: int = 90
    raid_join_rate_threshold: int = 8
    gate_duration_seconds: int = 900
    join_gate_mode: str = "timeout"


class Database:
    CONFIG_KEYS = {
        "log_channel_id",
        "welcome_channel_id",
        "rules_channel_id",
        "chat_channel_id",
        "help_channel_id",
        "about_channel_id",
        "perks_channel_id",
        "admin_role_name",
        "mod_role_name",
        "sync_mode",
        "sync_guild_id",
        "verification_url",
        "raid_detection_enabled",
        "raid_gate_threshold",
        "raid_monitor_window_seconds",
        "raid_join_rate_threshold",
        "gate_duration_seconds",
        "join_gate_mode",
    }

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL;")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS warnings (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    warning_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS warning_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    warning_count INTEGER NOT NULL,
                    reason TEXT,
                    channel_id INTEGER,
                    message_id INTEGER,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS moderation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    actor_user_id INTEGER,
                    target_user_id INTEGER,
                    action TEXT NOT NULL,
                    reason TEXT,
                    channel_id INTEGER,
                    message_id INTEGER,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id INTEGER PRIMARY KEY,
                    log_channel_id INTEGER,
                    welcome_channel_id INTEGER,
                    rules_channel_id INTEGER,
                    chat_channel_id INTEGER,
                    help_channel_id INTEGER,
                    about_channel_id INTEGER,
                    perks_channel_id INTEGER,
                    admin_role_name TEXT NOT NULL DEFAULT 'Admin',
                    mod_role_name TEXT NOT NULL DEFAULT 'Moderator',
                    sync_mode TEXT NOT NULL DEFAULT 'global',
                    sync_guild_id INTEGER,
                    verification_url TEXT,
                    raid_detection_enabled INTEGER NOT NULL DEFAULT 1,
                    raid_gate_threshold REAL NOT NULL DEFAULT 0.72,
                    raid_monitor_window_seconds INTEGER NOT NULL DEFAULT 90,
                    raid_join_rate_threshold INTEGER NOT NULL DEFAULT 8,
                    gate_duration_seconds INTEGER NOT NULL DEFAULT 900,
                    join_gate_mode TEXT NOT NULL DEFAULT 'timeout'
                );

                CREATE TABLE IF NOT EXISTS totp_secrets (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    secret TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_used_step INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS raid_state (
                    guild_id INTEGER PRIMARY KEY,
                    gate_active INTEGER NOT NULL DEFAULT 0,
                    gate_reason TEXT,
                    gate_until TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS verification_queue (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    risk_score REAL NOT NULL,
                    verification_url TEXT,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    verified_by_user_id INTEGER,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS join_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    account_age_days REAL NOT NULL,
                    has_avatar INTEGER NOT NULL,
                    profile_score REAL NOT NULL,
                    join_rate REAL NOT NULL,
                    young_account_ratio REAL NOT NULL,
                    risk_score REAL NOT NULL,
                    risk_level TEXT NOT NULL,
                    action TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._ensure_guild_config_columns(connection)
            connection.commit()

    def _ensure_guild_config_columns(self, connection: sqlite3.Connection) -> None:
        self._ensure_column(connection, "guild_config", "verification_url", "TEXT")
        self._ensure_column(
            connection,
            "guild_config",
            "raid_detection_enabled",
            "INTEGER NOT NULL DEFAULT 1",
        )
        self._ensure_column(
            connection,
            "guild_config",
            "raid_gate_threshold",
            "REAL NOT NULL DEFAULT 0.72",
        )
        self._ensure_column(
            connection,
            "guild_config",
            "raid_monitor_window_seconds",
            "INTEGER NOT NULL DEFAULT 90",
        )
        self._ensure_column(
            connection,
            "guild_config",
            "raid_join_rate_threshold",
            "INTEGER NOT NULL DEFAULT 8",
        )
        self._ensure_column(
            connection,
            "guild_config",
            "gate_duration_seconds",
            "INTEGER NOT NULL DEFAULT 900",
        )
        self._ensure_column(
            connection,
            "guild_config",
            "join_gate_mode",
            "TEXT NOT NULL DEFAULT 'timeout'",
        )

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        definition_sql: str,
    ) -> None:
        row = connection.execute(
            f"SELECT 1 FROM pragma_table_info('{table_name}') WHERE name = ?",
            (column_name,),
        ).fetchone()
        if row is not None:
            return
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition_sql}"
        )

    async def ensure_guild_config(self, guild_id: int) -> None:
        async with self._lock:
            await asyncio.to_thread(self._ensure_guild_config_sync, guild_id)

    def _ensure_guild_config_sync(self, guild_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)",
                (guild_id,),
            )
            connection.commit()

    async def get_guild_config(self, guild_id: int) -> GuildConfig:
        await self.ensure_guild_config(guild_id)
        return await asyncio.to_thread(self._get_guild_config_sync, guild_id)

    def _get_guild_config_sync(self, guild_id: int) -> GuildConfig:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM guild_config WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            if row is None:
                return GuildConfig(guild_id=guild_id)
            return GuildConfig(
                guild_id=row["guild_id"],
                log_channel_id=row["log_channel_id"],
                welcome_channel_id=row["welcome_channel_id"],
                rules_channel_id=row["rules_channel_id"],
                chat_channel_id=row["chat_channel_id"],
                help_channel_id=row["help_channel_id"],
                about_channel_id=row["about_channel_id"],
                perks_channel_id=row["perks_channel_id"],
                admin_role_name=row["admin_role_name"],
                mod_role_name=row["mod_role_name"],
                sync_mode=row["sync_mode"],
                sync_guild_id=row["sync_guild_id"],
                verification_url=row["verification_url"],
                raid_detection_enabled=bool(row["raid_detection_enabled"]),
                raid_gate_threshold=float(row["raid_gate_threshold"]),
                raid_monitor_window_seconds=int(row["raid_monitor_window_seconds"]),
                raid_join_rate_threshold=int(row["raid_join_rate_threshold"]),
                gate_duration_seconds=int(row["gate_duration_seconds"]),
                join_gate_mode=row["join_gate_mode"],
            )

    async def update_guild_config(self, guild_id: int, **updates: Any) -> GuildConfig:
        if not updates:
            return await self.get_guild_config(guild_id)

        invalid_keys = set(updates).difference(self.CONFIG_KEYS)
        if invalid_keys:
            invalid = ", ".join(sorted(invalid_keys))
            raise ValueError(f"Unsupported config key(s): {invalid}")

        if "sync_mode" in updates:
            mode = str(updates["sync_mode"]).lower().strip()
            if mode not in {"global", "guild"}:
                raise ValueError("sync_mode must be either 'global' or 'guild'")
            updates["sync_mode"] = mode

        if "join_gate_mode" in updates:
            mode = str(updates["join_gate_mode"]).lower().strip()
            if mode not in {"timeout", "kick"}:
                raise ValueError("join_gate_mode must be either 'timeout' or 'kick'")
            updates["join_gate_mode"] = mode

        if "raid_detection_enabled" in updates:
            updates["raid_detection_enabled"] = int(bool(updates["raid_detection_enabled"]))

        if "raid_gate_threshold" in updates:
            threshold = float(updates["raid_gate_threshold"])
            if threshold <= 0 or threshold > 1:
                raise ValueError("raid_gate_threshold must be between 0 and 1")
            updates["raid_gate_threshold"] = threshold

        if "raid_monitor_window_seconds" in updates:
            window = int(updates["raid_monitor_window_seconds"])
            if window < 15 or window > 600:
                raise ValueError("raid_monitor_window_seconds must be between 15 and 600")
            updates["raid_monitor_window_seconds"] = window

        if "raid_join_rate_threshold" in updates:
            threshold = int(updates["raid_join_rate_threshold"])
            if threshold < 2 or threshold > 100:
                raise ValueError("raid_join_rate_threshold must be between 2 and 100")
            updates["raid_join_rate_threshold"] = threshold

        if "gate_duration_seconds" in updates:
            duration = int(updates["gate_duration_seconds"])
            if duration < 60 or duration > 86400:
                raise ValueError("gate_duration_seconds must be between 60 and 86400")
            updates["gate_duration_seconds"] = duration

        await self.ensure_guild_config(guild_id)

        async with self._lock:
            await asyncio.to_thread(self._update_guild_config_sync, guild_id, updates)
        return await self.get_guild_config(guild_id)

    def _update_guild_config_sync(self, guild_id: int, updates: dict[str, Any]) -> None:
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = [updates[key] for key in updates]
        values.append(guild_id)

        with self._connect() as connection:
            connection.execute(
                f"UPDATE guild_config SET {assignments} WHERE guild_id = ?",
                values,
            )
            connection.commit()

    async def get_warning_count(self, guild_id: int, user_id: int) -> int:
        return await asyncio.to_thread(self._get_warning_count_sync, guild_id, user_id)

    def _get_warning_count_sync(self, guild_id: int, user_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT warning_count FROM warnings WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return int(row["warning_count"]) if row else 0

    async def increment_warning(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        channel_id: int | None = None,
        message_id: int | None = None,
    ) -> int:
        async with self._lock:
            return await asyncio.to_thread(
                self._increment_warning_sync,
                guild_id,
                user_id,
                reason,
                channel_id,
                message_id,
            )

    def _increment_warning_sync(
        self,
        guild_id: int,
        user_id: int,
        reason: str,
        channel_id: int | None,
        message_id: int | None,
    ) -> int:
        now = utc_now_iso()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT warning_count FROM warnings WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            new_count = (int(row["warning_count"]) if row else 0) + 1
            connection.execute(
                """
                INSERT INTO warnings (guild_id, user_id, warning_count, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET warning_count = excluded.warning_count, updated_at = excluded.updated_at
                """,
                (guild_id, user_id, new_count, now),
            )
            connection.execute(
                """
                INSERT INTO warning_events (
                    guild_id, user_id, warning_count, reason, channel_id, message_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, new_count, reason, channel_id, message_id, now),
            )
            connection.commit()
        return new_count

    async def reset_warnings(self, guild_id: int, user_id: int) -> None:
        async with self._lock:
            await asyncio.to_thread(self._reset_warnings_sync, guild_id, user_id)

    def _reset_warnings_sync(self, guild_id: int, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            connection.commit()

    async def log_moderation_action(
        self,
        guild_id: int,
        action: str,
        actor_user_id: int | None,
        target_user_id: int | None,
        reason: str | None = None,
        channel_id: int | None = None,
        message_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._log_moderation_action_sync,
                guild_id,
                action,
                actor_user_id,
                target_user_id,
                reason,
                channel_id,
                message_id,
                metadata,
            )

    def _log_moderation_action_sync(
        self,
        guild_id: int,
        action: str,
        actor_user_id: int | None,
        target_user_id: int | None,
        reason: str | None,
        channel_id: int | None,
        message_id: int | None,
        metadata: dict[str, Any] | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO moderation_logs (
                    guild_id, actor_user_id, target_user_id, action, reason,
                    channel_id, message_id, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    actor_user_id,
                    target_user_id,
                    action,
                    reason,
                    channel_id,
                    message_id,
                    json.dumps(metadata) if metadata else None,
                    utc_now_iso(),
                ),
            )
            connection.commit()

    async def get_recent_moderation_logs(
        self,
        guild_id: int,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 50))
        return await asyncio.to_thread(self._get_recent_moderation_logs_sync, guild_id, limit)

    def _get_recent_moderation_logs_sync(self, guild_id: int, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, actor_user_id, target_user_id, action, reason,
                       channel_id, message_id, metadata, created_at
                FROM moderation_logs
                WHERE guild_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()
            output: list[dict[str, Any]] = []
            for row in rows:
                parsed_metadata: dict[str, Any] | None = None
                if row["metadata"]:
                    try:
                        parsed_metadata = json.loads(row["metadata"])
                    except json.JSONDecodeError:
                        parsed_metadata = {"raw": row["metadata"]}
                output.append(
                    {
                        "id": row["id"],
                        "actor_user_id": row["actor_user_id"],
                        "target_user_id": row["target_user_id"],
                        "action": row["action"],
                        "reason": row["reason"],
                        "channel_id": row["channel_id"],
                        "message_id": row["message_id"],
                        "metadata": parsed_metadata,
                        "created_at": row["created_at"],
                    }
                )
            return output

    async def set_totp_secret(self, guild_id: int, user_id: int, secret: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._set_totp_secret_sync, guild_id, user_id, secret)

    def _set_totp_secret_sync(self, guild_id: int, user_id: int, secret: str) -> None:
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO totp_secrets (guild_id, user_id, secret, enabled, last_used_step, created_at, updated_at)
                VALUES (?, ?, ?, 1, NULL, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET secret = excluded.secret, enabled = 1, last_used_step = NULL, updated_at = excluded.updated_at
                """,
                (guild_id, user_id, secret, now, now),
            )
            connection.commit()

    async def get_totp_secret(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_totp_secret_sync, guild_id, user_id)

    def _get_totp_secret_sync(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT secret, enabled, last_used_step
                FROM totp_secrets
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()
            if row is None:
                return None
            return {
                "secret": row["secret"],
                "enabled": bool(row["enabled"]),
                "last_used_step": row["last_used_step"],
            }

    async def disable_totp_secret(self, guild_id: int, user_id: int) -> None:
        async with self._lock:
            await asyncio.to_thread(self._disable_totp_secret_sync, guild_id, user_id)

    def _disable_totp_secret_sync(self, guild_id: int, user_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE totp_secrets
                SET enabled = 0, updated_at = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (utc_now_iso(), guild_id, user_id),
            )
            connection.commit()

    async def set_totp_last_used_step(
        self,
        guild_id: int,
        user_id: int,
        used_step: int,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._set_totp_last_used_step_sync,
                guild_id,
                user_id,
                used_step,
            )

    def _set_totp_last_used_step_sync(self, guild_id: int, user_id: int, used_step: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE totp_secrets
                SET last_used_step = ?, updated_at = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (used_step, utc_now_iso(), guild_id, user_id),
            )
            connection.commit()

    async def log_join_event(
        self,
        guild_id: int,
        user_id: int,
        account_age_days: float,
        has_avatar: bool,
        profile_score: float,
        join_rate: float,
        young_account_ratio: float,
        risk_score: float,
        risk_level: str,
        action: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._log_join_event_sync,
                guild_id,
                user_id,
                account_age_days,
                has_avatar,
                profile_score,
                join_rate,
                young_account_ratio,
                risk_score,
                risk_level,
                action,
                metadata,
            )

    def _log_join_event_sync(
        self,
        guild_id: int,
        user_id: int,
        account_age_days: float,
        has_avatar: bool,
        profile_score: float,
        join_rate: float,
        young_account_ratio: float,
        risk_score: float,
        risk_level: str,
        action: str,
        metadata: dict[str, Any] | None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO join_events (
                    guild_id, user_id, account_age_days, has_avatar, profile_score,
                    join_rate, young_account_ratio, risk_score, risk_level, action,
                    metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    account_age_days,
                    int(has_avatar),
                    profile_score,
                    join_rate,
                    young_account_ratio,
                    risk_score,
                    risk_level,
                    action,
                    json.dumps(metadata) if metadata else None,
                    utc_now_iso(),
                ),
            )
            connection.commit()

    async def upsert_verification_member(
        self,
        guild_id: int,
        user_id: int,
        status: str,
        risk_score: float,
        verification_url: str | None,
        reason: str,
        verified_by_user_id: int | None = None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._upsert_verification_member_sync,
                guild_id,
                user_id,
                status,
                risk_score,
                verification_url,
                reason,
                verified_by_user_id,
            )

    def _upsert_verification_member_sync(
        self,
        guild_id: int,
        user_id: int,
        status: str,
        risk_score: float,
        verification_url: str | None,
        reason: str,
        verified_by_user_id: int | None,
    ) -> None:
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO verification_queue (
                    guild_id, user_id, status, risk_score, verification_url, reason,
                    created_at, updated_at, verified_by_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET
                    status = excluded.status,
                    risk_score = excluded.risk_score,
                    verification_url = excluded.verification_url,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at,
                    verified_by_user_id = excluded.verified_by_user_id
                """,
                (
                    guild_id,
                    user_id,
                    status,
                    risk_score,
                    verification_url,
                    reason,
                    now,
                    now,
                    verified_by_user_id,
                ),
            )
            connection.commit()

    async def get_verification_status(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_verification_status_sync, guild_id, user_id)

    def _get_verification_status_sync(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status, risk_score, verification_url, reason, created_at, updated_at, verified_by_user_id
                FROM verification_queue
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()
            if row is None:
                return None
            return {
                "status": row["status"],
                "risk_score": float(row["risk_score"]),
                "verification_url": row["verification_url"],
                "reason": row["reason"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "verified_by_user_id": row["verified_by_user_id"],
            }

    async def is_member_pending_verification(self, guild_id: int, user_id: int) -> bool:
        status = await self.get_verification_status(guild_id, user_id)
        if status is None:
            return False
        return status["status"] == "pending"

    async def set_raid_gate_state(
        self,
        guild_id: int,
        gate_active: bool,
        reason: str | None,
        gate_until: str | None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._set_raid_gate_state_sync,
                guild_id,
                gate_active,
                reason,
                gate_until,
            )

    def _set_raid_gate_state_sync(
        self,
        guild_id: int,
        gate_active: bool,
        reason: str | None,
        gate_until: str | None,
    ) -> None:
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO raid_state (guild_id, gate_active, gate_reason, gate_until, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id)
                DO UPDATE SET
                    gate_active = excluded.gate_active,
                    gate_reason = excluded.gate_reason,
                    gate_until = excluded.gate_until,
                    updated_at = excluded.updated_at
                """,
                (guild_id, int(gate_active), reason, gate_until, now),
            )
            connection.commit()

    async def get_raid_gate_state(self, guild_id: int) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_raid_gate_state_sync, guild_id)

    def _get_raid_gate_state_sync(self, guild_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT gate_active, gate_reason, gate_until, updated_at
                FROM raid_state
                WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
            if row is None:
                return {
                    "gate_active": False,
                    "gate_reason": None,
                    "gate_until": None,
                    "updated_at": None,
                }
            return {
                "gate_active": bool(row["gate_active"]),
                "gate_reason": row["gate_reason"],
                "gate_until": row["gate_until"],
                "updated_at": row["updated_at"],
            }

    async def list_pending_verifications(self, guild_id: int, limit: int = 15) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 50))
        return await asyncio.to_thread(self._list_pending_verifications_sync, guild_id, limit)

    def _list_pending_verifications_sync(self, guild_id: int, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id, status, risk_score, verification_url, reason, created_at, updated_at, verified_by_user_id
                FROM verification_queue
                WHERE guild_id = ? AND status = 'pending'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()
            output: list[dict[str, Any]] = []
            for row in rows:
                output.append(
                    {
                        "user_id": row["user_id"],
                        "status": row["status"],
                        "risk_score": float(row["risk_score"]),
                        "verification_url": row["verification_url"],
                        "reason": row["reason"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "verified_by_user_id": row["verified_by_user_id"],
                    }
                )
            return output

    async def get_recent_join_events(self, guild_id: int, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        return await asyncio.to_thread(self._get_recent_join_events_sync, guild_id, limit)

    def _get_recent_join_events_sync(self, guild_id: int, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id, account_age_days, has_avatar, profile_score,
                       join_rate, young_account_ratio, risk_score, risk_level,
                       action, metadata, created_at
                FROM join_events
                WHERE guild_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()

            output: list[dict[str, Any]] = []
            for row in rows:
                parsed_metadata: dict[str, Any] | None = None
                if row["metadata"]:
                    try:
                        parsed_metadata = json.loads(row["metadata"])
                    except json.JSONDecodeError:
                        parsed_metadata = {"raw": row["metadata"]}

                output.append(
                    {
                        "user_id": row["user_id"],
                        "account_age_days": float(row["account_age_days"]),
                        "has_avatar": bool(row["has_avatar"]),
                        "profile_score": float(row["profile_score"]),
                        "join_rate": float(row["join_rate"]),
                        "young_account_ratio": float(row["young_account_ratio"]),
                        "risk_score": float(row["risk_score"]),
                        "risk_level": row["risk_level"],
                        "action": row["action"],
                        "metadata": parsed_metadata,
                        "created_at": row["created_at"],
                    }
                )
            return output
