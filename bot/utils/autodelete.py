from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

import fluxer


DEFAULT_AUTO_DELETE_COMMANDS = {
    "warnings",
    "kick",
    "ban",
    "mute",
    "unmute",
    "ctxkick",
    "ctxban",
    "ctxmute",
    "ctxunmute",
    "purge",
    "addbadword",
    "removebadword",
    "viewbadwords",
    "viewbadwordsnext",
    "viewbadwordsprev",
    "reloadwords",
    "addrole",
    "removerole",
    "ctxaddrole",
    "ctxremoverole",
    "setlogchannel",
    "setwelcomechannel",
    "setresourcechannels",
    "setroles",
    "setsyncmode",
    "serverconfig",
    "adminhelp",
}


class AutoDeleteManager:
    def __init__(
        self,
        *,
        enabled: bool = True,
        delay_seconds: int = 10,
        command_names: Iterable[str] | None = None,
    ) -> None:
        self.enabled = enabled
        self.delay_seconds = max(1, int(delay_seconds))
        if command_names is None:
            self.command_names = set(DEFAULT_AUTO_DELETE_COMMANDS)
        else:
            self.command_names = {name.strip().lower() for name in command_names if name.strip()}

    def should_auto_delete(self, command_name: str | None) -> bool:
        if not self.enabled:
            return False
        if not command_name:
            return False
        return command_name.strip().lower() in self.command_names

    async def reply(
        self,
        ctx: fluxer.Message,
        *,
        content: str | None = None,
        embed: fluxer.Embed | None = None,
        command_name: str | None = None,
        force: bool = False,
    ) -> fluxer.Message:
        sent = await ctx.reply(content=content, embed=embed)
        if force or self.should_auto_delete(command_name):
            self.schedule_delete(ctx)
            self.schedule_delete(sent)
        return sent

    def schedule_delete(self, message: fluxer.Message, delay_seconds: int | None = None) -> None:
        delay = self.delay_seconds if delay_seconds is None else max(1, int(delay_seconds))

        async def _delete_later() -> None:
            await asyncio.sleep(delay)
            try:
                await message.delete()
            except Exception:
                # Ignore: permissions, already deleted, or unsupported contexts.
                pass

        asyncio.create_task(_delete_later())


def parse_command_name_from_message(
    message_content: str,
    prefixes: str | Iterable[str],
) -> str | None:
    content = message_content.strip()
    if not content:
        return None

    if isinstance(prefixes, str):
        prefix_list = [prefixes]
    else:
        prefix_list = [prefix for prefix in prefixes]

    for prefix in sorted(prefix_list, key=len, reverse=True):
        if not prefix:
            continue
        if content.startswith(prefix):
            without_prefix = content[len(prefix) :].strip()
            if not without_prefix:
                return None
            return without_prefix.split()[0].lower()
    return None


def parse_auto_delete_commands(raw: str | None) -> set[str]:
    if not raw:
        return set(DEFAULT_AUTO_DELETE_COMMANDS)
    parsed = {item.strip().lower() for item in raw.split(",") if item.strip()}
    return parsed or set(DEFAULT_AUTO_DELETE_COMMANDS)
