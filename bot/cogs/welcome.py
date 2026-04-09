from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, cast

import fluxer
from fluxer import Cog

from utils.checks import is_staff_member
from utils.embeds import info_embed, warning_embed, welcome_embed

logger = logging.getLogger(__name__)


class WelcomeCog(Cog):
    def __init__(self, bot: fluxer.Bot) -> None:
        super().__init__(bot)
        runtime_bot = cast(Any, bot)
        self.db = runtime_bot.db
        self.config_cache = runtime_bot.config_cache
        self._channel_name_cache: dict[tuple[int, str], tuple[int, float]] = {}
        self._cache_ttl = 300

    async def _find_channel_id_by_name(self, guild_id: int, channel_name: str) -> int | None:
        key = (guild_id, channel_name.lower())
        now = time.time()
        cached = self._channel_name_cache.get(key)
        if cached and now < cached[1]:
            return cached[0]

        http_client = getattr(self.bot, "_http", None)
        if http_client is None:
            return None

        try:
            channels = await http_client.get_guild_channels(guild_id)
        except Exception:
            logger.exception("Failed to fetch guild channels for welcome setup")
            return None

        for channel in channels:
            if channel.get("name", "").lower() == channel_name.lower():
                channel_id = int(channel["id"])
                self._channel_name_cache[key] = (channel_id, now + self._cache_ttl)
                return channel_id
        return None

    async def _resolve_resource_channel_ids(self, guild_id: int) -> dict[str, int | None]:
        config = await self.config_cache.get(guild_id)

        rules_id = config.rules_channel_id or await self._find_channel_id_by_name(guild_id, "rules")
        chat_id = config.chat_channel_id or await self._find_channel_id_by_name(guild_id, "chat")
        help_id = config.help_channel_id or await self._find_channel_id_by_name(guild_id, "help")
        about_id = config.about_channel_id or await self._find_channel_id_by_name(guild_id, "about")
        perks_id = config.perks_channel_id or await self._find_channel_id_by_name(guild_id, "perks")

        return {
            "rules": rules_id,
            "chat": chat_id,
            "help": help_id,
            "about": about_id,
            "perks": perks_id,
        }

    async def send_welcome_for_member(self, member: fluxer.GuildMember) -> None:
        if member.user.bot or member.guild_id is None:
            return

        guild_id = int(member.guild_id)
        config = await self.config_cache.get(guild_id)

        welcome_channel_id = config.welcome_channel_id
        if welcome_channel_id is None:
            welcome_channel_id = await self._find_channel_id_by_name(guild_id, "welcome")
        if welcome_channel_id is None:
            logger.info("Welcome channel not configured and no #welcome channel found")
            return

        guild = self.bot.get_guild(guild_id)
        guild_name = guild.name if guild and guild.name else f"Guild {guild_id}"

        resources = await self._resolve_resource_channel_ids(guild_id)
        links_lines = [
            f"Rules: <#{resources['rules']}>" if resources["rules"] else "Rules: set with /setresourcechannels",
            f"Chat: <#{resources['chat']}>" if resources["chat"] else "Chat: set with /setresourcechannels",
            "Help: run /help",
            "About: run /aboutserver",
            "Perks: run /perks",
        ]
        links_text = "\n".join(f"- {line}" for line in links_lines)

        embed = welcome_embed(member.display_name, guild_name, links_text)
        if member.user.avatar_url:
            embed.set_thumbnail(url=member.user.avatar_url)

        try:
            channel = await self.bot.fetch_channel(str(welcome_channel_id))
            await channel.send(embed=embed)
        except Exception:
            logger.exception("Failed to send welcome message")

    @Cog.listener(name="on_member_join")
    async def on_member_join(self, payload: Any) -> None:
        member: fluxer.GuildMember
        if isinstance(payload, fluxer.GuildMember):
            member = payload
        elif isinstance(payload, dict):
            member = fluxer.GuildMember.from_data(payload, getattr(self.bot, "_http", None))
        else:
            return

        if member.user.bot or member.guild_id is None:
            return

        guild_id = int(member.guild_id)

        # Allow anti-raid listener to persist gate/verification state first.
        await asyncio.sleep(1)

        gate_state = await self.db.get_raid_gate_state(guild_id)
        if gate_state.get("gate_active"):
            logger.info("Skipping welcome while raid gate is active for guild %s", guild_id)
            return

        if await self.db.is_member_pending_verification(guild_id, member.user.id):
            logger.info("Skipping welcome for pending verification member %s", member.user.id)
            return

        await self.send_welcome_for_member(member)

    def _general_help_text(self) -> str:
        return (
            "Use /help <category> to view grouped commands.\n\n"
            "Available categories:\n"
            "- admin\n"
            "- moderation\n"
            "- security\n"
            "- testing\n"
            "- ai\n"
            "- welcome\n\n"
            "Staff-only categories are delivered in DM: admin, moderation, security, testing.\n\n"
            "Examples:\n"
            "- /help admin\n"
            "- /help moderation\n"
            "- /help testing\n\n"
            "Note: Fluxer currently does not expose button-interaction UI, so help is category-based text embeds."
        )

    async def _send_staff_help_dm(self, ctx: fluxer.Message, title: str, description: str) -> None:
        if ctx.guild_id is None:
            await ctx.reply(
                embed=warning_embed(
                    "Server Only",
                    "This staff help category must be requested from a server channel.",
                )
            )
            return

        try:
            if not await is_staff_member(self.bot, ctx):
                await ctx.reply(
                    embed=warning_embed(
                        "Staff Only",
                        "This help category is only available to moderators and admins.",
                    )
                )
                return
        except Exception:
            logger.exception("Failed to evaluate staff membership for help command")
            await ctx.reply(
                embed=warning_embed(
                    "Check Failed",
                    "Could not verify your staff access right now. Try again in a moment.",
                )
            )
            return

        try:
            if hasattr(ctx.author, "send"):
                await ctx.author.send(embed=info_embed(title, description))
                await ctx.reply(
                    embed=info_embed(
                        "Help Sent In DM",
                        "This category contains staff commands, so it was sent privately.",
                    )
                )
                return
        except Exception:
            logger.exception("Failed to send staff help DM")

        await ctx.reply(
            embed=warning_embed(
                "DM Required",
                "I could not deliver that help section in DM. Enable direct messages and try again.",
            )
        )

    def _admin_help_text(self) -> str:
        return (
            "Admin commands:\n"
            "- /addbadword <word_or_phrase>\n"
            "- /removebadword <word_or_phrase>\n"
            "- /viewbadwords [page]\n"
            "- /viewbadwordsnext\n"
            "- /viewbadwordsprev\n"
            "- /reloadwords\n"
            "- /setlogchannel <channel_id>\n"
            "- /setwelcomechannel <channel_id>\n"
            "- /setresourcechannels <rules> <chat> <help> <about> <perks>\n"
            "- /setroles AdminRole | ModRole\n"
            "- /setsyncmode <global|guild> [guild_id]\n"
            "- /serverconfig\n"
            "- /setverificationurl <https://...|off>\n"
            "- /setraidsettings <threshold> <join_rate_threshold> <window_seconds> <gate_duration_seconds> <timeout|kick>\n"
            "- /setraiddetection <on|off>\n"
            "- /raidgate <on|off|status> [duration_seconds]"
        )

    def _moderation_help_text(self) -> str:
        return (
            "Moderation commands:\n"
            "- /warnings [user]\n"
            "- /kick <user> <reason> --confirm\n"
            "- /ban <user> <reason> --confirm\n"
            "- /mute <user> [minutes] [reason]\n"
            "- /unmute <user> [reason]\n"
            "- /ctxkick <reason> --confirm\n"
            "- /ctxban <reason> --confirm\n"
            "- /ctxmute [minutes] [reason]\n"
            "- /ctxunmute [reason]\n"
            "- /purge <count>\n"
            "\nSecurity note: moderation/admin commands require TOTP monthly confirmation.\n"
            "Use /totpauth <code> once every 30 days (or include --totp <code> on a command)."
        )

    def _security_help_text(self) -> str:
        return (
            "Security commands:\n"
            "- /totpsetup\n"
            "- /totpreset\n"
            "- /totpauth <6-digit-code> (refreshes 30-day access window)\n"
            "- /totpdisable <6-digit-code>\n"
            "- /pendingverifications [limit]\n"
            "- /verifyjoin <user>\n"
            "- /rejectjoin <user> [reason]\n"
            "- /raidsnapshot [limit]\n"
            "\nRaid drill tip: run /help testing for a step-by-step testing sequence.\n"
            "\nClosed testing note: raid detection and verification flow may change while stabilized."
        )

    def _testing_help_text(self) -> str:
        return (
            "Raid Testing Quickstart:\n"
            "1) Authenticate staff account\n"
            "- /totpsetup (first time only)\n"
            "- /totpauth <6-digit-code>\n\n"
            "2) Configure channels and verification mode\n"
            "- /setlogchannel <channel_id>\n"
            "- /setwelcomechannel <channel_id>\n"
            "- /setverificationurl off (use this if you have no website)\n\n"
            "3) Make raid detection easier to trigger in tests\n"
            "- /setraidsettings 0.60 3 120 300 timeout\n"
            "- /setraiddetection on\n\n"
            "4) Force a gate window to test immediately\n"
            "- /raidgate on 300\n"
            "- Have 2-3 alt/new accounts join during the gate\n\n"
            "5) Review and resolve queue\n"
            "- /pendingverifications\n"
            "- /verifyjoin <user> or /rejectjoin <user> [reason]\n"
            "- /raidsnapshot [limit]\n"
            "- /raidgate off\n\n"
            "Note: /totpauth is required again when the 30-day window expires."
        )

    def _ai_help_text(self) -> str:
        return (
            "AI commands:\n"
            "- /ask <question>\n"
            "- /joke\n"
            "- /aistatus"
        )

    def _welcome_help_text(self) -> str:
        return (
            "Welcome commands:\n"
            "- /help [category]\n"
            "- /helpmenu\n"
            "- /aboutserver\n"
            "- /perks"
        )

    @Cog.command(name="help")
    async def help_command(self, ctx: fluxer.Message, category: str = "") -> None:
        chosen = category.strip().lower()

        if chosen in {"", "main", "menu", "all"}:
            await ctx.reply(embed=info_embed("Help", self._general_help_text()))
            return

        if chosen in {"admin", "admins"}:
            await self._send_staff_help_dm(ctx, "Help: Admin", self._admin_help_text())
            return

        if chosen in {"moderation", "mod", "mods"}:
            await self._send_staff_help_dm(ctx, "Help: Moderation", self._moderation_help_text())
            return

        if chosen in {"security", "sec", "totp", "raid"}:
            await self._send_staff_help_dm(ctx, "Help: Security", self._security_help_text())
            return

        if chosen in {"testing", "test", "raidtest", "raidtesting"}:
            await self._send_staff_help_dm(ctx, "Help: Testing", self._testing_help_text())
            return

        if chosen in {"ai", "gemini"}:
            await ctx.reply(embed=info_embed("Help: AI", self._ai_help_text()))
            return

        if chosen in {"welcome", "utility"}:
            await ctx.reply(embed=info_embed("Help: Welcome", self._welcome_help_text()))
            return

        await ctx.reply(
            embed=warning_embed(
                "Unknown Help Category",
                "Try /help testing, /help admin, or /help moderation, or run /help for the full menu.",
            )
        )

    @Cog.command(name="helpmenu")
    async def help_menu(self, ctx: fluxer.Message) -> None:
        await self.help_command(ctx)

    @Cog.command(name="aboutserver")
    async def about_server(self, ctx: fluxer.Message) -> None:
        text = getattr(
            self.bot,
            "about_text",
            "This server is managed with automation-first moderation and utility tooling.",
        )
        await ctx.reply(embed=info_embed("About", text))

    @Cog.command(name="perks")
    async def perks(self, ctx: fluxer.Message) -> None:
        text = getattr(
            self.bot,
            "perks_text",
            "Participate in the community to unlock role-based perks and channels.",
        )
        await ctx.reply(embed=info_embed("Perks", text))


async def setup(bot: fluxer.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
