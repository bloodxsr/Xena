from __future__ import annotations

import asyncio
import functools
import inspect
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, TypeVar, cast

import fluxer

from utils.autodelete import parse_command_name_from_message
from database.db import Database, GuildConfig

USER_ID_REGEX = re.compile(r"\d{5,22}")
ROLE_ID_REGEX = re.compile(r"\d{5,22}")
CommandHandler = TypeVar("CommandHandler", bound=Callable[..., Coroutine[Any, Any, None]])


@dataclass(slots=True)
class CacheEntry:
    expires_at: float
    value: GuildConfig


class ConfigCache:
    def __init__(self, db: Database, ttl_seconds: int = 45) -> None:
        self.db = db
        self.ttl_seconds = ttl_seconds
        self._cache: dict[int, CacheEntry] = {}

    async def get(self, guild_id: int) -> GuildConfig:
        now = time.time()
        existing = self._cache.get(guild_id)
        if existing and existing.expires_at > now:
            return existing.value

        fresh = await self.db.get_guild_config(guild_id)
        self._cache[guild_id] = CacheEntry(expires_at=now + self.ttl_seconds, value=fresh)
        return fresh

    async def refresh(self, guild_id: int) -> GuildConfig:
        fresh = await self.db.get_guild_config(guild_id)
        self._cache[guild_id] = CacheEntry(expires_at=time.time() + self.ttl_seconds, value=fresh)
        return fresh

    def invalidate(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)


class _ReplyProxy:
    def __init__(self, message: fluxer.Message, reply_handler: Callable[..., Coroutine[Any, Any, fluxer.Message]]) -> None:
        self._message = message
        self._reply_handler = reply_handler

    def __getattr__(self, name: str) -> Any:
        return getattr(self._message, name)

    async def reply(self, *args: Any, **kwargs: Any) -> fluxer.Message:
        return await self._reply_handler(*args, **kwargs)


def require_totp(func: CommandHandler) -> CommandHandler:
    setattr(func, "__force_totp__", True)
    return func


def skip_totp(func: CommandHandler) -> CommandHandler:
    setattr(func, "__skip_totp__", True)
    return func


def has_permission(permission: fluxer.Permissions) -> Callable[[CommandHandler], CommandHandler]:
    """Permission check wrapper with signature preservation and optional auto-delete integration."""

    def decorator(func: CommandHandler) -> CommandHandler:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> None:
            a: list[Any] = list(args)
            is_cog_method = bool(a and hasattr(a[0], "bot"))
            ctx: fluxer.Message = a[1] if is_cog_method else a[0]
            bot_obj: fluxer.Bot | None = a[0].bot if is_cog_method else None
            call_kwargs = dict(kwargs)

            command_name: str | None = None
            auto_delete = getattr(bot_obj, "auto_delete", None)
            if bot_obj is not None:
                prefixes = getattr(bot_obj, "command_prefix", "/")
                if callable(prefixes):
                    prefixes = ["/", "!"]
                command_name = parse_command_name_from_message(ctx.content, prefixes)

            should_auto_delete = bool(
                auto_delete is not None
                and command_name is not None
                and auto_delete.should_auto_delete(command_name)
            )

            async def reply_with_auto_delete(*reply_args: Any, **reply_kwargs: Any) -> fluxer.Message:
                sent = await ctx.reply(*reply_args, **reply_kwargs)
                if should_auto_delete and auto_delete is not None:
                    auto_delete.schedule_delete(sent)
                return sent

            if ctx.guild_id is None:
                await reply_with_auto_delete("This command can only be used in a server.")
                return

            if ctx._http is None:
                raise RuntimeError("HTTPClient is required to check permissions")

            guild_data, member_data, roles_data = await asyncio.gather(
                ctx._http.get_guild(ctx.guild_id),
                ctx._http.get_guild_member(ctx.guild_id, ctx.author.id),
                ctx._http.get_guild_roles(ctx.guild_id),
            )

            if ctx.author.id == int(guild_data["owner_id"]):
                permitted = True
            else:
                member_role_ids = {int(r) for r in member_data.get("roles", [])}
                computed = 0
                for role in roles_data:
                    role_id = int(role["id"])
                    if role_id == int(ctx.guild_id) or role_id in member_role_ids:
                        computed |= int(role.get("permissions", 0))

                if computed & int(fluxer.Permissions.ADMINISTRATOR):
                    permitted = True
                else:
                    permitted = (computed & int(permission)) == int(permission)

            if not permitted:
                await reply_with_auto_delete("You don't have permission to use this command.")
                if should_auto_delete and auto_delete is not None:
                    auto_delete.schedule_delete(ctx)
                return

            requires_totp = False
            if is_cog_method:
                cog_name = type(a[0]).__name__
                requires_totp = cog_name in {"AdminCog", "ModerationCog"}

            if getattr(func, "__force_totp__", False):
                requires_totp = True
            if getattr(func, "__skip_totp__", False):
                requires_totp = False

            if requires_totp and ctx.guild_id is not None:
                totp_manager = getattr(bot_obj, "totp_auth", None)
                if totp_manager is None:
                    await reply_with_auto_delete(
                        "Security layer is unavailable. This command is blocked until TOTP is restored."
                    )
                    return

                is_enrolled = await totp_manager.is_registered(ctx.guild_id, ctx.author.id)
                if not is_enrolled:
                    await reply_with_auto_delete(
                        "TOTP is not set up for your staff account yet. Run /totpsetup first, add the secret to an authenticator app, then run /totpauth <6-digit-code> to enable privileged commands."
                    )
                    return

                code = totp_manager.extract_totp_code(ctx.content or "")

                if code is None:
                    has_recent_auth = await totp_manager.has_recent_totp_auth(ctx.guild_id, ctx.author.id)
                    if not has_recent_auth:
                        await reply_with_auto_delete(
                            "TOTP monthly confirmation required. Run /totpauth <6-digit-code> once every 30 days, or append --totp <code> to this command to verify now."
                        )
                        return
                else:
                    verified, verify_reason = await totp_manager.verify_command_totp(
                        ctx.guild_id,
                        ctx.author.id,
                        code,
                        issue_grant=False,
                    )
                    if not verified:
                        await reply_with_auto_delete(f"TOTP verification failed: {verify_reason}")
                        return

                if code is not None:
                    for key, value in call_kwargs.items():
                        if isinstance(value, str):
                            call_kwargs[key] = totp_manager.strip_totp_flag(value)

            call_args = a
            if should_auto_delete:
                if auto_delete is not None:
                    auto_delete.schedule_delete(ctx)
                proxy = _ReplyProxy(ctx, reply_with_auto_delete)
                if is_cog_method:
                    call_args[1] = proxy
                else:
                    call_args[0] = proxy

            await func(*call_args, **call_kwargs)

        try:
            wrapper.__signature__ = inspect.signature(func)  # type: ignore[attr-defined]
        except (TypeError, ValueError):
            pass

        for attr in ("__cog_command__", "__cog_command_name__"):
            if hasattr(func, attr):
                setattr(wrapper, attr, getattr(func, attr))

        return cast(CommandHandler, wrapper)

    return decorator


def parse_user_id(value: str | None) -> int | None:
    if not value:
        return None
    match = USER_ID_REGEX.search(value)
    if not match:
        return None
    return int(match.group(0))


def parse_role_id(value: str | None) -> int | None:
    if not value:
        return None
    match = ROLE_ID_REGEX.search(value)
    if not match:
        return None
    return int(match.group(0))


def mention_user(user_id: int) -> str:
    return f"<@{user_id}>"


async def resolve_target_user_id(ctx: fluxer.Message, target: str | None) -> int | None:
    parsed = parse_user_id(target)
    if parsed is not None:
        return parsed
    if ctx.referenced_message is not None:
        return ctx.referenced_message.author.id
    return None


async def resolve_role_id(guild: fluxer.Guild, role_input: str) -> int | None:
    role_id = parse_role_id(role_input)
    if role_id is not None:
        return role_id

    roles = await guild.fetch_roles()
    wanted = role_input.strip().lower()
    for role in roles:
        if role.name.lower() == wanted:
            return role.id
    return None


async def compute_member_permissions(message: fluxer.Message) -> int:
    if message.guild_id is None or message._http is None:
        return 0

    guild_data, member_data, roles_data = await asyncio.gather(
        message._http.get_guild(message.guild_id),
        message._http.get_guild_member(message.guild_id, message.author.id),
        message._http.get_guild_roles(message.guild_id),
    )

    if message.author.id == int(guild_data["owner_id"]):
        return int(fluxer.Permissions.ADMINISTRATOR)

    member_role_ids = {int(role_id) for role_id in member_data.get("roles", [])}
    computed = 0
    for role in roles_data:
        role_id = int(role["id"])
        if role_id == int(message.guild_id) or role_id in member_role_ids:
            computed |= int(role.get("permissions", 0))

    return computed


async def is_staff_member(bot: fluxer.Bot, message: fluxer.Message) -> bool:
    if message.guild_id is None:
        return False

    permissions = await compute_member_permissions(message)
    if permissions & int(fluxer.Permissions.ADMINISTRATOR):
        return True
    if permissions & int(fluxer.Permissions.MANAGE_MESSAGES):
        return True
    if permissions & int(fluxer.Permissions.MODERATE_MEMBERS):
        return True

    config_cache = getattr(bot, "config_cache", None)
    if config_cache is None:
        return False

    guild = await bot.fetch_guild(str(message.guild_id))
    member = await guild.fetch_member(message.author.id)
    role_ids = set(member.roles)

    config = await config_cache.get(message.guild_id)
    if not config.admin_role_name and not config.mod_role_name:
        return False

    roles = await guild.fetch_roles()
    names_by_id = {role.id: role.name.lower() for role in roles}
    admin_name = config.admin_role_name.strip().lower()
    mod_name = config.mod_role_name.strip().lower()

    for role_id in role_ids:
        role_name = names_by_id.get(role_id, "")
        if role_name == admin_name or role_name == mod_name:
            return True

    return False


def truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."
