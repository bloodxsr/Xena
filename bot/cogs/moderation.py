from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, cast

import fluxer
from fluxer import Cog

from utils.checks import has_permission, is_staff_member, mention_user, resolve_target_user_id
from utils.embeds import (
    error_embed,
    moderation_log_embed,
    success_embed,
    warning_embed,
    warnings_status_embed,
)

logger = logging.getLogger(__name__)


class ModerationCog(Cog):
    def __init__(self, bot: fluxer.Bot) -> None:
        super().__init__(bot)
        runtime_bot = cast(Any, bot)
        self.db = runtime_bot.db
        self.word_store = runtime_bot.word_store
        self.config_cache = runtime_bot.config_cache
        self.max_warnings = int(getattr(runtime_bot, "max_warnings", 4))

    @Cog.listener(name="on_message")
    async def on_message(self, message: fluxer.Message) -> None:
        if message.author.bot:
            return
        if message.guild_id is None:
            return
        if not message.content.strip():
            return

        try:
            if await is_staff_member(self.bot, message):
                return
        except Exception:
            logger.exception("Failed to run staff bypass check")

        if not self.word_store.matches(message.content):
            return

        await self._handle_automod_violation(message)

    async def _handle_automod_violation(self, message: fluxer.Message) -> None:
        if message.guild_id is None:
            return
        guild_id = message.guild_id
        reason = "Blacklisted content detected"

        try:
            await message.delete()
        except Exception:
            logger.exception("Could not delete violating message")

        warning_count = await self.db.increment_warning(
            guild_id=guild_id,
            user_id=message.author.id,
            reason=reason,
            channel_id=message.channel_id,
            message_id=message.id,
        )

        escalation_note = {
            1: "Warning 1 issued.",
            2: "Warning 2 issued.",
            3: "Final warning issued. Next violation triggers kick.",
        }.get(warning_count, "Kick threshold reached.")

        await self.db.log_moderation_action(
            guild_id=guild_id,
            action="automod_warning",
            actor_user_id=self.bot.user.id if self.bot.user else None,
            target_user_id=message.author.id,
            reason=f"{reason} | count={warning_count}",
            channel_id=message.channel_id,
            message_id=message.id,
        )

        if warning_count >= self.max_warnings:
            await self._kick_for_warnings(message, warning_count)
            return

        warning_message = (
            f"{mention_user(message.author.id)} your message was removed for policy violations.\n"
            f"Warnings: {warning_count}/{self.max_warnings}\n"
            f"{escalation_note}"
        )
        await message.send(embed=warning_embed("Automated Moderation", warning_message))

        await self._send_log(
            guild_id,
            moderation_log_embed(
                action="automod_warning",
                target_user_id=message.author.id,
                actor_user_id=self.bot.user.id if self.bot.user else None,
                reason=f"{reason} ({warning_count}/{self.max_warnings})",
            ),
        )

    async def _kick_for_warnings(self, message: fluxer.Message, warning_count: int) -> None:
        if message.guild_id is None:
            return
        guild_id = message.guild_id
        kick_reason = "Exceeded warning limit from automated moderation"
        kicked = False

        try:
            guild = await self.bot.fetch_guild(str(guild_id))
            member = await guild.fetch_member(message.author.id)
            await member.kick(reason=kick_reason)
            kicked = True
        except Exception:
            logger.exception("Failed to kick user after max warnings")

        if kicked:
            await self.db.reset_warnings(guild_id, message.author.id)
            await message.send(
                embed=warning_embed(
                    "Escalation Triggered",
                    f"{mention_user(message.author.id)} has been kicked after reaching {warning_count} warnings.",
                )
            )
            await self.db.log_moderation_action(
                guild_id=guild_id,
                action="automod_kick",
                actor_user_id=self.bot.user.id if self.bot.user else None,
                target_user_id=message.author.id,
                reason=kick_reason,
                channel_id=message.channel_id,
                message_id=message.id,
            )
            await self._send_log(
                guild_id,
                moderation_log_embed(
                    action="automod_kick",
                    target_user_id=message.author.id,
                    actor_user_id=self.bot.user.id if self.bot.user else None,
                    reason=kick_reason,
                ),
            )
        else:
            await message.send(
                embed=error_embed(
                    "Escalation Failed",
                    "Maximum warnings reached, but the bot could not kick the user. Check permissions.",
                )
            )

    async def _send_log(self, guild_id: int, embed: fluxer.Embed) -> None:
        try:
            config = await self.config_cache.get(guild_id)
            if not config.log_channel_id:
                return
            log_channel = await self.bot.fetch_channel(str(config.log_channel_id))
            await log_channel.send(embed=embed)
        except Exception:
            logger.exception("Failed to send moderation log message")

    async def _resolve_member(self, ctx: fluxer.Message, target: str | None) -> fluxer.GuildMember | None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return None

        user_id = await resolve_target_user_id(ctx, target)
        if user_id is None:
            await ctx.reply(
                embed=error_embed(
                    "Missing Target",
                    "Provide a user ID or mention, or reply to a message and run the command again.",
                )
            )
            return None

        guild = await self.bot.fetch_guild(str(ctx.guild_id))
        try:
            return await guild.fetch_member(user_id)
        except Exception:
            await ctx.reply(embed=error_embed("User Not Found", "Could not find that user in this server."))
            return None

    async def _can_moderate_target(
        self,
        ctx: fluxer.Message,
        target: fluxer.GuildMember,
    ) -> tuple[bool, str]:
        if ctx.guild_id is None:
            return False, "Command must be used in a server."

        if target.user.id == ctx.author.id:
            return False, "You cannot moderate yourself."

        guild = await self.bot.fetch_guild(str(ctx.guild_id))
        if guild.owner_id == target.user.id:
            return False, "You cannot moderate the server owner."

        if self.bot.user and target.user.id == self.bot.user.id:
            return False, "You cannot moderate the bot account."

        actor = await guild.fetch_member(ctx.author.id)
        if guild.owner_id == actor.user.id:
            return True, ""

        roles = await guild.fetch_roles()
        role_positions = {role.id: role.position for role in roles}

        actor_top = max((role_positions.get(role_id, 0) for role_id in actor.roles), default=0)
        target_top = max((role_positions.get(role_id, 0) for role_id in target.roles), default=0)

        if actor_top <= target_top:
            return False, "You cannot moderate a user with equal or higher role hierarchy."

        return True, ""

    @staticmethod
    def _extract_confirmation(reason: str) -> tuple[bool, str]:
        token = "--confirm"
        if token not in reason:
            return False, reason.strip()
        cleaned = reason.replace(token, "").strip()
        return True, cleaned or "No reason provided"

    async def _run_member_action(
        self,
        ctx: fluxer.Message,
        target: str,
        reason: str,
        action: Literal["kick", "ban"],
    ) -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        guild_id = ctx.guild_id

        member = await self._resolve_member(ctx, target)
        if member is None:
            return

        allowed, error = await self._can_moderate_target(ctx, member)
        if not allowed:
            await ctx.reply(embed=error_embed("Permission Denied", error))
            return

        confirmed, clean_reason = self._extract_confirmation(reason)
        if not confirmed:
            await ctx.reply(
                embed=warning_embed(
                    "Confirmation Required",
                    f"Re-run the command with --confirm to execute {action}. Example: /{action} {member.user.id} {clean_reason} --confirm",
                )
            )
            return

        if action == "kick":
            await member.kick(reason=clean_reason)
            action_label = "kicked"
        else:
            await member.ban(reason=clean_reason)
            action_label = "banned"

        await self.db.log_moderation_action(
            guild_id=guild_id,
            action=action,
            actor_user_id=ctx.author.id,
            target_user_id=member.user.id,
            reason=clean_reason,
            channel_id=ctx.channel_id,
            message_id=ctx.id,
        )

        await ctx.reply(
            embed=success_embed(
                f"{action.title()} Successful",
                f"{mention_user(member.user.id)} has been {action_label}.\nReason: {clean_reason}",
            )
        )

        await self._send_log(
            guild_id,
            moderation_log_embed(
                action=action,
                target_user_id=member.user.id,
                actor_user_id=ctx.author.id,
                reason=clean_reason,
            ),
        )

    @Cog.command(name="warnings")
    @has_permission(fluxer.Permissions.MANAGE_MESSAGES)
    async def warnings(self, ctx: fluxer.Message, target: str = "") -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        guild_id = ctx.guild_id

        target_id = await resolve_target_user_id(ctx, target)
        if target_id is None:
            target_id = ctx.author.id

        count = await self.db.get_warning_count(guild_id, target_id)
        await ctx.reply(embed=warnings_status_embed(target_id, count, self.max_warnings))

    @Cog.command(name="kick")
    @has_permission(fluxer.Permissions.KICK_MEMBERS)
    async def kick(self, ctx: fluxer.Message, target: str = "", *, reason: str = "No reason provided") -> None:
        await self._run_member_action(ctx, target, reason, "kick")

    @Cog.command(name="ctxkick")
    @has_permission(fluxer.Permissions.KICK_MEMBERS)
    async def ctxkick(self, ctx: fluxer.Message, *, reason: str = "No reason provided --confirm") -> None:
        await self._run_member_action(ctx, "", reason, "kick")

    @Cog.command(name="ban")
    @has_permission(fluxer.Permissions.BAN_MEMBERS)
    async def ban(self, ctx: fluxer.Message, target: str = "", *, reason: str = "No reason provided") -> None:
        await self._run_member_action(ctx, target, reason, "ban")

    @Cog.command(name="ctxban")
    @has_permission(fluxer.Permissions.BAN_MEMBERS)
    async def ctxban(self, ctx: fluxer.Message, *, reason: str = "No reason provided --confirm") -> None:
        await self._run_member_action(ctx, "", reason, "ban")

    @Cog.command(name="mute")
    @has_permission(fluxer.Permissions.MODERATE_MEMBERS)
    async def mute(
        self,
        ctx: fluxer.Message,
        target: str = "",
        duration_minutes: int = 10,
        *,
        reason: str = "No reason provided",
    ) -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        guild_id = ctx.guild_id

        member = await self._resolve_member(ctx, target)
        if member is None:
            return

        allowed, error = await self._can_moderate_target(ctx, member)
        if not allowed:
            await ctx.reply(embed=error_embed("Permission Denied", error))
            return

        duration_minutes = max(1, min(duration_minutes, 10080))
        until = (datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)).isoformat()

        await member.timeout(until=until, reason=reason)

        await self.db.log_moderation_action(
            guild_id=guild_id,
            action="mute",
            actor_user_id=ctx.author.id,
            target_user_id=member.user.id,
            reason=reason,
            channel_id=ctx.channel_id,
            message_id=ctx.id,
            metadata={"duration_minutes": duration_minutes, "until": until},
        )

        await ctx.reply(
            embed=success_embed(
                "Mute Applied",
                f"{mention_user(member.user.id)} has been muted for {duration_minutes} minute(s).",
            )
        )

        await self._send_log(
            guild_id,
            moderation_log_embed(
                action="mute",
                target_user_id=member.user.id,
                actor_user_id=ctx.author.id,
                reason=f"{reason} | duration={duration_minutes}m",
            ),
        )

    @Cog.command(name="ctxmute")
    @has_permission(fluxer.Permissions.MODERATE_MEMBERS)
    async def ctxmute(
        self,
        ctx: fluxer.Message,
        duration_minutes: int = 10,
        *,
        reason: str = "No reason provided",
    ) -> None:
        await self.mute(ctx, "", duration_minutes, reason=reason)

    @Cog.command(name="unmute")
    @has_permission(fluxer.Permissions.MODERATE_MEMBERS)
    async def unmute(self, ctx: fluxer.Message, target: str = "", *, reason: str = "No reason provided") -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return
        guild_id = ctx.guild_id

        member = await self._resolve_member(ctx, target)
        if member is None:
            return

        await member.timeout(until=None, reason=reason)

        await self.db.log_moderation_action(
            guild_id=guild_id,
            action="unmute",
            actor_user_id=ctx.author.id,
            target_user_id=member.user.id,
            reason=reason,
            channel_id=ctx.channel_id,
            message_id=ctx.id,
        )

        await ctx.reply(
            embed=success_embed(
                "Mute Removed",
                f"{mention_user(member.user.id)} has been unmuted.",
            )
        )

        await self._send_log(
            guild_id,
            moderation_log_embed(
                action="unmute",
                target_user_id=member.user.id,
                actor_user_id=ctx.author.id,
                reason=reason,
            ),
        )

    @Cog.command(name="ctxunmute")
    @has_permission(fluxer.Permissions.MODERATE_MEMBERS)
    async def ctxunmute(self, ctx: fluxer.Message, *, reason: str = "No reason provided") -> None:
        await self.unmute(ctx, "", reason=reason)

    @Cog.command(name="purge")
    @has_permission(fluxer.Permissions.MANAGE_MESSAGES)
    async def purge(self, ctx: fluxer.Message, amount: int | str = 10) -> None:
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            await ctx.reply(embed=error_embed("Invalid Amount", "Amount must be a number."))
            return

        if amount < 1:
            await ctx.reply(embed=error_embed("Invalid Amount", "Amount must be at least 1."))
            return
        if amount > 100:
            await ctx.reply(embed=error_embed("Invalid Amount", "Amount cannot exceed 100."))
            return

        if ctx.channel is None:
            await ctx.reply(embed=error_embed("Missing Channel", "Could not resolve current channel."))
            return

        try:
            messages = await ctx.channel.fetch_messages(limit=min(amount + 10, 100))
        except Exception:
            logger.exception("Failed to fetch messages for purge")
            await ctx.reply(
                embed=error_embed(
                    "Purge Failed",
                    "Could not fetch channel messages. Check bot permissions and try again.",
                )
            )
            return

        candidates = [msg for msg in messages if msg.id != ctx.id]
        to_delete = candidates[:amount]

        if not to_delete:
            await ctx.reply(embed=warning_embed("Nothing To Delete", "No messages were found to delete."))
            return

        deleted_count = 0

        # Bulk-delete is efficient but may fail for edge cases (single message, old messages, pinned messages).
        if len(to_delete) >= 2:
            bulk_ids: list[int | str] = []
            for message in to_delete:
                bulk_ids.append(str(message.id))
            try:
                await ctx.channel.delete_messages(bulk_ids)
                deleted_count = len(to_delete)
            except Exception:
                logger.exception("Bulk delete failed, falling back to per-message delete")

        if deleted_count < len(to_delete):
            for message in to_delete[deleted_count:]:
                try:
                    await message.delete()
                    deleted_count += 1
                except Exception:
                    logger.debug("Could not delete message %s during purge fallback", message.id, exc_info=True)

        if deleted_count == 0:
            await ctx.reply(
                embed=error_embed(
                    "Purge Failed",
                    "No messages could be deleted. Ensure the bot has Manage Messages permission.",
                )
            )
            return

        if deleted_count < len(to_delete):
            await ctx.reply(
                embed=warning_embed(
                    "Purge Partial",
                    f"Deleted {deleted_count}/{len(to_delete)} message(s). Some messages could not be removed.",
                )
            )
            return

        await ctx.reply(embed=success_embed("Messages Deleted", f"Deleted {deleted_count} message(s)."))


async def setup(bot: fluxer.Bot) -> None:
    await bot.add_cog(ModerationCog(bot))
