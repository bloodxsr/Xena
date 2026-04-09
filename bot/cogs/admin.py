from __future__ import annotations

import logging
from typing import Any, cast

import fluxer
from fluxer import Cog

from utils.checks import has_permission, mention_user, resolve_role_id, resolve_target_user_id
from utils.embeds import blacklist_page_embed, error_embed, info_embed, success_embed, warning_embed
from utils.paginator import PaginatorManager

logger = logging.getLogger(__name__)


class AdminCog(Cog):
    def __init__(self, bot: fluxer.Bot) -> None:
        super().__init__(bot)
        runtime_bot = cast(Any, bot)
        self.db = runtime_bot.db
        self.word_store = runtime_bot.word_store
        self.config_cache = runtime_bot.config_cache
        self.paginator = PaginatorManager(page_size=20, session_timeout=600)

    async def _resolve_member(self, ctx: fluxer.Message, target: str | None) -> fluxer.GuildMember | None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return None

        user_id = await resolve_target_user_id(ctx, target)
        if user_id is None:
            await ctx.reply(embed=error_embed("Missing Target", "Provide a user ID/mention or reply to a user message."))
            return None

        guild = await self.bot.fetch_guild(str(ctx.guild_id))
        try:
            return await guild.fetch_member(user_id)
        except Exception:
            await ctx.reply(embed=error_embed("User Not Found", "That user could not be found in this server."))
            return None

    def _build_blacklist_embed_for_session(self, session) -> fluxer.Embed:
        return blacklist_page_embed(
            words=session.get_page_items(),
            page=session.page_number,
            total_pages=session.total_pages,
            total_words=len(session.items),
        )

    async def _update_and_refresh_config(self, guild_id: int, **updates) -> None:
        await self.db.update_guild_config(guild_id, **updates)
        self.config_cache.invalidate(guild_id)
        await self.config_cache.refresh(guild_id)

    @Cog.command(name="addbadword")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def add_bad_word(self, ctx: fluxer.Message, *, word: str = "") -> None:
        cleaned = word.strip().lower()
        if not cleaned:
            await ctx.reply(embed=error_embed("Invalid Input", "Provide a valid word or phrase."))
            return

        inserted = await self.word_store.add_word(cleaned)
        if not inserted:
            await ctx.reply(embed=warning_embed("No Change", "That word is already in the blacklist."))
            return

        await ctx.reply(embed=success_embed("Blacklist Updated", f"Added '{cleaned}' to blacklist."))

    @Cog.command(name="removebadword")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def remove_bad_word(self, ctx: fluxer.Message, *, word: str = "") -> None:
        cleaned = word.strip().lower()
        if not cleaned:
            await ctx.reply(embed=error_embed("Invalid Input", "Provide a valid word or phrase."))
            return

        removed = await self.word_store.remove_word(cleaned)
        if not removed:
            await ctx.reply(embed=warning_embed("Not Found", "That word does not exist in the blacklist."))
            return

        await ctx.reply(embed=success_embed("Blacklist Updated", f"Removed '{cleaned}' from blacklist."))

    @Cog.command(name="viewbadwords")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def view_bad_words(self, ctx: fluxer.Message, page: int = 1) -> None:
        words = self.word_store.as_sorted_list()
        if not words:
            await ctx.reply(embed=info_embed("Blacklist", "Blacklist is currently empty."))
            return

        session = self.paginator.start(ctx.author.id, words)
        page = max(1, page)
        session.page_index = min(page - 1, session.total_pages - 1)

        await ctx.reply(embed=self._build_blacklist_embed_for_session(session))

    @Cog.command(name="viewbadwordsnext")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def view_bad_words_next(self, ctx: fluxer.Message) -> None:
        session = self.paginator.next_page(ctx.author.id)
        if session is None:
            await ctx.reply(
                embed=warning_embed(
                    "No Active Session",
                    "Start with /viewbadwords before using navigation commands.",
                )
            )
            return

        await ctx.reply(embed=self._build_blacklist_embed_for_session(session))

    @Cog.command(name="viewbadwordsprev")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def view_bad_words_prev(self, ctx: fluxer.Message) -> None:
        session = self.paginator.previous_page(ctx.author.id)
        if session is None:
            await ctx.reply(
                embed=warning_embed(
                    "No Active Session",
                    "Start with /viewbadwords before using navigation commands.",
                )
            )
            return

        await ctx.reply(embed=self._build_blacklist_embed_for_session(session))

    @Cog.command(name="reloadwords")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def reload_words(self, ctx: fluxer.Message) -> None:
        words = await self.word_store.reload()
        await ctx.reply(embed=success_embed("Blacklist Reloaded", f"Loaded {len(words)} word(s) from words.py."))

    @Cog.command(name="addrole")
    @has_permission(fluxer.Permissions.MANAGE_ROLES)
    async def add_role(self, ctx: fluxer.Message, target: str = "", *, role: str = "") -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        guild_id = ctx.guild_id

        if not role.strip():
            await ctx.reply(embed=error_embed("Missing Role", "Provide a role ID, role mention, or exact role name."))
            return

        member = await self._resolve_member(ctx, target)
        if member is None:
            return

        guild = await self.bot.fetch_guild(str(guild_id))
        role_id = await resolve_role_id(guild, role)
        if role_id is None:
            await ctx.reply(embed=error_embed("Role Not Found", "Could not resolve the requested role."))
            return

        await member.add_role(role_id, reason=f"Added by {ctx.author.id}")
        await ctx.reply(embed=success_embed("Role Added", f"Added role {role_id} to {mention_user(member.user.id)}."))

        await self.db.log_moderation_action(
            guild_id=guild_id,
            action="add_role",
            actor_user_id=ctx.author.id,
            target_user_id=member.user.id,
            reason=f"role_id={role_id}",
            channel_id=ctx.channel_id,
            message_id=ctx.id,
        )

    @Cog.command(name="removerole")
    @has_permission(fluxer.Permissions.MANAGE_ROLES)
    async def remove_role(self, ctx: fluxer.Message, target: str = "", *, role: str = "") -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        guild_id = ctx.guild_id

        if not role.strip():
            await ctx.reply(embed=error_embed("Missing Role", "Provide a role ID, role mention, or exact role name."))
            return

        member = await self._resolve_member(ctx, target)
        if member is None:
            return

        guild = await self.bot.fetch_guild(str(guild_id))
        role_id = await resolve_role_id(guild, role)
        if role_id is None:
            await ctx.reply(embed=error_embed("Role Not Found", "Could not resolve the requested role."))
            return

        await member.remove_role(role_id, reason=f"Removed by {ctx.author.id}")
        await ctx.reply(embed=success_embed("Role Removed", f"Removed role {role_id} from {mention_user(member.user.id)}."))

        await self.db.log_moderation_action(
            guild_id=guild_id,
            action="remove_role",
            actor_user_id=ctx.author.id,
            target_user_id=member.user.id,
            reason=f"role_id={role_id}",
            channel_id=ctx.channel_id,
            message_id=ctx.id,
        )

    @Cog.command(name="ctxaddrole")
    @has_permission(fluxer.Permissions.MANAGE_ROLES)
    async def ctx_add_role(self, ctx: fluxer.Message, *, role: str = "") -> None:
        await self.add_role(ctx, "", role=role)

    @Cog.command(name="ctxremoverole")
    @has_permission(fluxer.Permissions.MANAGE_ROLES)
    async def ctx_remove_role(self, ctx: fluxer.Message, *, role: str = "") -> None:
        await self.remove_role(ctx, "", role=role)

    @Cog.command(name="setlogchannel")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def set_log_channel(self, ctx: fluxer.Message, channel_id: int) -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        guild_id = ctx.guild_id

        await self._update_and_refresh_config(guild_id, log_channel_id=channel_id)
        await ctx.reply(embed=success_embed("Config Updated", f"Log channel set to {channel_id}."))

    @Cog.command(name="setwelcomechannel")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def set_welcome_channel(self, ctx: fluxer.Message, channel_id: int) -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        guild_id = ctx.guild_id

        await self._update_and_refresh_config(guild_id, welcome_channel_id=channel_id)
        await ctx.reply(embed=success_embed("Config Updated", f"Welcome channel set to {channel_id}."))

    @Cog.command(name="setresourcechannels")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def set_resource_channels(
        self,
        ctx: fluxer.Message,
        rules_channel_id: int,
        chat_channel_id: int,
        help_channel_id: int,
        about_channel_id: int,
        perks_channel_id: int,
    ) -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        guild_id = ctx.guild_id

        await self._update_and_refresh_config(
            guild_id,
            rules_channel_id=rules_channel_id,
            chat_channel_id=chat_channel_id,
            help_channel_id=help_channel_id,
            about_channel_id=about_channel_id,
            perks_channel_id=perks_channel_id,
        )
        await ctx.reply(embed=success_embed("Config Updated", "Resource channel IDs updated successfully."))

    @Cog.command(name="setroles")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def set_roles(self, ctx: fluxer.Message, *, role_names: str = "") -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        guild_id = ctx.guild_id

        parts = [part.strip() for part in role_names.split("|") if part.strip()]
        if len(parts) != 2:
            await ctx.reply(
                embed=error_embed(
                    "Invalid Format",
                    "Use: /setroles AdminRoleName | ModeratorRoleName",
                )
            )
            return

        await self._update_and_refresh_config(
            guild_id,
            admin_role_name=parts[0],
            mod_role_name=parts[1],
        )
        await ctx.reply(embed=success_embed("Config Updated", "Admin and moderator role names were updated."))

    @Cog.command(name="setsyncmode")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def set_sync_mode(self, ctx: fluxer.Message, mode: str, guild_id: int = 0) -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        server_guild_id = ctx.guild_id

        mode = mode.strip().lower()
        if mode not in {"global", "guild"}:
            await ctx.reply(embed=error_embed("Invalid Mode", "Mode must be either 'global' or 'guild'."))
            return

        sync_guild_id = guild_id if mode == "guild" and guild_id > 0 else None
        await self._update_and_refresh_config(
            server_guild_id,
            sync_mode=mode,
            sync_guild_id=sync_guild_id,
        )

        note = "No API sync call is required for Fluxer prefix commands."
        await ctx.reply(embed=info_embed("Sync Mode Updated", f"sync_mode={mode}, sync_guild_id={sync_guild_id}\n{note}"))

    @Cog.command(name="serverconfig")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def server_config(self, ctx: fluxer.Message) -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        guild_id = ctx.guild_id

        config = await self.config_cache.get(guild_id)
        embed = info_embed("Server Config", "Cached guild configuration values")
        embed.add_field(name="log_channel_id", value=str(config.log_channel_id), inline=True)
        embed.add_field(name="welcome_channel_id", value=str(config.welcome_channel_id), inline=True)
        embed.add_field(name="rules_channel_id", value=str(config.rules_channel_id), inline=True)
        embed.add_field(name="chat_channel_id", value=str(config.chat_channel_id), inline=True)
        embed.add_field(name="help_channel_id", value=str(config.help_channel_id), inline=True)
        embed.add_field(name="about_channel_id", value=str(config.about_channel_id), inline=True)
        embed.add_field(name="perks_channel_id", value=str(config.perks_channel_id), inline=True)
        embed.add_field(name="admin_role_name", value=config.admin_role_name, inline=True)
        embed.add_field(name="mod_role_name", value=config.mod_role_name, inline=True)
        embed.add_field(name="sync_mode", value=config.sync_mode, inline=True)
        embed.add_field(name="sync_guild_id", value=str(config.sync_guild_id), inline=True)
        embed.add_field(name="verification_url", value=str(config.verification_url), inline=False)
        embed.add_field(name="raid_detection_enabled", value=str(config.raid_detection_enabled), inline=True)
        embed.add_field(name="raid_gate_threshold", value=str(config.raid_gate_threshold), inline=True)
        embed.add_field(name="raid_monitor_window_seconds", value=str(config.raid_monitor_window_seconds), inline=True)
        embed.add_field(name="raid_join_rate_threshold", value=str(config.raid_join_rate_threshold), inline=True)
        embed.add_field(name="gate_duration_seconds", value=str(config.gate_duration_seconds), inline=True)
        embed.add_field(name="join_gate_mode", value=str(config.join_gate_mode), inline=True)
        await ctx.reply(embed=embed)

    @Cog.command(name="adminhelp")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def admin_help(self, ctx: fluxer.Message) -> None:
        help_text = (
            "Security prerequisites:\n"
            "- /totpsetup (one-time enrollment)\n"
            "- /totpreset (rotate/reset your secret)\n"
            "- /totpauth <code> once every 30 days\n\n"
            "Blacklist:\n"
            "- /addbadword <word>\n"
            "- /removebadword <word>\n"
            "- /viewbadwords [page]\n"
            "- /viewbadwordsnext\n"
            "- /viewbadwordsprev\n"
            "- /reloadwords\n\n"
            "Roles:\n"
            "- /addrole <user> <role>\n"
            "- /removerole <user> <role>\n"
            "- /ctxaddrole <role> (reply-context)\n"
            "- /ctxremoverole <role> (reply-context)\n\n"
            "Config:\n"
            "- /setlogchannel <channel_id>\n"
            "- /setwelcomechannel <channel_id>\n"
            "- /setresourcechannels <rules> <chat> <help> <about> <perks>\n"
            "- /setroles AdminRole | ModRole\n"
            "- /setsyncmode <global|guild> [guild_id]\n"
            "- /serverconfig\n"
            "- /setverificationurl <https://...|off>\n"
            "- /setraidsettings <threshold> <join_rate_threshold> <window_seconds> <gate_duration_seconds> <timeout|kick>\n"
            "- /setraiddetection <on|off>\n"
            "- /raidgate <on|off|status> [duration_seconds]\n"
            "- /pendingverifications [limit]\n"
            "- /verifyjoin <user>\n"
            "- /rejectjoin <user> [reason]\n"
            "- /raidsnapshot [limit]"
        )
        await ctx.reply(embed=info_embed("Admin Commands", help_text))


async def setup(bot: fluxer.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
