from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Any
from urllib.parse import quote

from database.db import Database

TOTP_FLAG = "--totp"
DEFAULT_MONTHLY_AUTH_WINDOW_DAYS = 30


class TotpAuthManager:
    def __init__(
        self,
        db: Database,
        issuer_name: str = "FluxerBot",
        period_seconds: int = 30,
        monthly_auth_window_days: int = DEFAULT_MONTHLY_AUTH_WINDOW_DAYS,
    ) -> None:
        self.db = db
        self.issuer_name = issuer_name
        self.period_seconds = period_seconds
        self.digits = 6
        self.window_steps = 1
        self.monthly_auth_window_days = max(1, int(monthly_auth_window_days))
        self.monthly_auth_window_seconds = self.monthly_auth_window_days * 24 * 60 * 60
        self.monthly_auth_window_steps = max(1, self.monthly_auth_window_seconds // self.period_seconds)

    async def register_user(self, guild_id: int, user_id: int) -> tuple[str, str]:
        secret = self.generate_secret()
        await self.db.set_totp_secret(guild_id, user_id, secret)
        uri = self.build_provisioning_uri(secret, account_name=str(user_id))
        return secret, uri

    async def disable_user(self, guild_id: int, user_id: int) -> None:
        await self.db.disable_totp_secret(guild_id, user_id)

    async def is_registered(self, guild_id: int, user_id: int) -> bool:
        data = await self.db.get_totp_secret(guild_id, user_id)
        return bool(data and data.get("enabled"))

    async def verify_command_totp(
        self,
        guild_id: int,
        user_id: int,
        code: str,
        issue_grant: bool = False,
    ) -> tuple[bool, str]:
        if not code or not code.isdigit() or len(code) != self.digits:
            return False, "TOTP code must be a 6-digit number."

        data = await self.db.get_totp_secret(guild_id, user_id)
        if data is None or not data.get("enabled"):
            return False, "No active TOTP enrollment found for your account."

        secret = str(data["secret"])
        last_used_step = data.get("last_used_step")

        now_step = int(time.time()) // self.period_seconds
        matched_step: int | None = None
        for offset in range(-self.window_steps, self.window_steps + 1):
            step = now_step + offset
            expected = self.generate_code_for_step(secret, step)
            if hmac.compare_digest(expected, code):
                matched_step = step
                break

        if matched_step is None:
            return False, "Invalid TOTP code."

        if last_used_step is not None and matched_step <= int(last_used_step):
            return False, "This TOTP code was already used. Wait for a new code and try again."

        await self.db.set_totp_last_used_step(guild_id, user_id, matched_step)
        return True, "ok"

    async def has_recent_totp_auth(self, guild_id: int, user_id: int) -> bool:
        data = await self.db.get_totp_secret(guild_id, user_id)
        if data is None or not data.get("enabled"):
            return False

        last_used_step = data.get("last_used_step")
        if last_used_step is None:
            return False

        now_step = int(time.time()) // self.period_seconds
        age_steps = now_step - int(last_used_step)
        return age_steps <= self.monthly_auth_window_steps

    def has_pending_command_grant(self, guild_id: int, user_id: int) -> bool:
        # Legacy compatibility shim. Per-command grants were replaced by
        # a persisted monthly re-auth window based on last_used_step.
        return False

    def consume_command_grant(self, guild_id: int, user_id: int) -> bool:
        # Legacy compatibility shim.
        return False

    def invalidate_session(self, guild_id: int, user_id: int) -> None:
        # Legacy compatibility shim.
        return None

    @staticmethod
    def extract_totp_code(command_text: str) -> str | None:
        lowered = command_text.lower()
        marker = f" {TOTP_FLAG} "
        if marker in lowered:
            idx = lowered.rfind(marker)
            candidate = command_text[idx + len(marker) :].strip().split(" ", 1)[0]
            if candidate.isdigit() and len(candidate) == 6:
                return candidate

        starts = f"{TOTP_FLAG} "
        if lowered.startswith(starts):
            candidate = command_text[len(starts) :].strip().split(" ", 1)[0]
            if candidate.isdigit() and len(candidate) == 6:
                return candidate

        return None

    @staticmethod
    def strip_totp_flag(text: str) -> str:
        pieces = text.split()
        if not pieces:
            return text

        cleaned: list[str] = []
        index = 0
        while index < len(pieces):
            token = pieces[index]
            if token.lower() == TOTP_FLAG and index + 1 < len(pieces):
                next_token = pieces[index + 1]
                if next_token.isdigit() and len(next_token) == 6:
                    index += 2
                    continue
            cleaned.append(token)
            index += 1

        return " ".join(cleaned).strip()

    def generate_secret(self) -> str:
        raw = secrets.token_bytes(20)
        return base64.b32encode(raw).decode("ascii").rstrip("=")

    def build_provisioning_uri(self, secret: str, account_name: str) -> str:
        issuer = quote(self.issuer_name)
        account = quote(account_name)
        return (
            f"otpauth://totp/{issuer}:{account}"
            f"?secret={secret}&issuer={issuer}&algorithm=SHA1&digits={self.digits}&period={self.period_seconds}"
        )

    def generate_code_for_step(self, secret: str, step: int) -> str:
        secret_bytes = self._decode_secret(secret)
        message = step.to_bytes(8, byteorder="big", signed=False)
        digest = hmac.new(secret_bytes, message, hashlib.sha1).digest()

        offset = digest[-1] & 0x0F
        binary = (
            ((digest[offset] & 0x7F) << 24)
            | (digest[offset + 1] << 16)
            | (digest[offset + 2] << 8)
            | digest[offset + 3]
        )
        code_int = binary % (10**self.digits)
        return str(code_int).zfill(self.digits)

    @staticmethod
    def _decode_secret(secret: str) -> bytes:
        normalized = secret.strip().replace(" ", "").upper()
        pad_len = (-len(normalized)) % 8
        normalized = normalized + ("=" * pad_len)
        return base64.b32decode(normalized, casefold=True)
