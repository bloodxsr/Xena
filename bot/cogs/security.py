from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import fluxer
from fluxer import Cog

from utils.checks import (
    has_permission,
    is_staff_member,
    mention_user,
    require_totp,
    resolve_target_user_id,
)
from utils.embeds import error_embed, info_embed, success_embed, warning_embed
from utils.raid_signals import JoinRiskSignal, RaidRiskEngine

logger = logging.getLogger(__name__)
DISCORD_EPOCH_MS = 1420070400000


class SecurityCog(Cog):
    def __init__(self, bot: fluxer.Bot) -> None:
        super().__init__(bot)
        runtime_bot = cast(Any, bot)
        self.db = runtime_bot.db
        self.config_cache = runtime_bot.config_cache
        self.totp_auth = runtime_bot.totp_auth
        self.risk_engine = RaidRiskEngine()

    async def _send_security_log(self, guild_id: int, title: str, description: str, level: str = "info") -> None:
        try:
            config = await self.config_cache.get(guild_id)
            if not config.log_channel_id:
                return

            channel = await self.bot.fetch_channel(str(config.log_channel_id))
            if level == "error":
                embed = error_embed(title, description)
            elif level == "warning":
                embed = warning_embed(title, description)
            else:
                embed = info_embed(title, description)
            await channel.send(embed=embed)
        except Exception:
            logger.exception("Failed to send security log")

    @staticmethod
    def _parse_member_payload(payload: Any, bot: fluxer.Bot) -> fluxer.GuildMember | None:
        if isinstance(payload, fluxer.GuildMember):
            return payload
        if isinstance(payload, dict):
            return fluxer.GuildMember.from_data(payload, getattr(bot, "_http", None))
        return None

    @staticmethod
    def _snowflake_to_datetime(user_id: int) -> datetime:
        timestamp_ms = (int(user_id) >> 22) + DISCORD_EPOCH_MS
        return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)

    def _estimate_profile_score(self, member: fluxer.GuildMember) -> tuple[float, bool]:
        user = member.user
        has_avatar = bool(getattr(user, "avatar_url", None))
        display_name = str(getattr(member, "display_name", "") or "").strip()
        username = str(getattr(user, "username", "") or "").strip()
        name_to_score = display_name or username

        score = 0.0
        if has_avatar:
            score += 0.55
        if len(name_to_score) >= 3:
            score += 0.25
        if name_to_score and not name_to_score.lower().startswith(("user", "member", "new")):
            score += 0.20

        return min(1.0, max(0.0, score)), has_avatar

    async def _get_effective_gate_state(self, guild_id: int) -> dict[str, Any]:
        state = await self.db.get_raid_gate_state(guild_id)
        if not state.get("gate_active"):
            return state

        gate_until_text = state.get("gate_until")
        if not gate_until_text:
            return state

        try:
            gate_until = datetime.fromisoformat(gate_until_text)
        except Exception:
            return state

        if gate_until.tzinfo is None:
            gate_until = gate_until.replace(tzinfo=timezone.utc)

        if datetime.now(timezone.utc) >= gate_until:
            await self.db.set_raid_gate_state(guild_id, False, "Gate expired", None)
            return {
                "gate_active": False,
                "gate_reason": "Gate expired",
                "gate_until": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

        return state

    async def _send_verification_dm(self, member: fluxer.GuildMember, verification_url: str | None, reason: str) -> bool:
        if verification_url:
            message = (
                "Your join is currently gated while staff review activity patterns.\n"
                f"Reason: {reason}\n"
                f"Complete verification here: {verification_url}\n"
                "Closed testing note: some verification flows may be unstable. If the site fails, contact staff."
            )
        else:
            message = (
                "Your join is currently gated while staff review activity patterns.\n"
                f"Reason: {reason}\n"
                "No verification website is configured right now.\n"
                "A staff member will review and approve or reject your join manually."
            )

        try:
            author = member.user
            if hasattr(author, "send"):
                await author.send(message)
                return True
        except Exception:
            logger.debug("Failed to DM verification link to user", exc_info=True)

        return False

    async def _gate_member(
        self,
        member: fluxer.GuildMember,
        signal: JoinRiskSignal,
        reason: str,
        verification_url: str | None,
        gate_mode: str,
        gate_duration_seconds: int,
    ) -> str:
        now = datetime.now(timezone.utc)
        gate_until = now + timedelta(seconds=gate_duration_seconds)
        user_id = member.user.id
        if member.guild_id is None:
            return "gated_failed"
        guild_id = int(member.guild_id)

        await self.db.upsert_verification_member(
            guild_id=guild_id,
            user_id=user_id,
            status="pending",
            risk_score=signal.risk_score,
            verification_url=verification_url,
            reason=reason,
        )

        dm_sent = await self._send_verification_dm(member, verification_url, reason)
        metadata = {
            "risk_score": round(signal.risk_score, 4),
            "risk_level": signal.risk_level,
            "join_rate_per_minute": round(signal.join_rate_per_minute, 3),
            "young_account_ratio": round(signal.young_account_ratio, 3),
            "dm_sent": dm_sent,
            "gate_mode": gate_mode,
        }

        if gate_mode == "kick":
            try:
                await member.kick(reason=f"Join gated during security review: {reason}")
                await self.db.log_moderation_action(
                    guild_id=guild_id,
                    action="join_gate_kick",
                    actor_user_id=self.bot.user.id if self.bot.user else None,
                    target_user_id=user_id,
                    reason=reason,
                    metadata=metadata,
                )
                return "gated_kick"
            except Exception:
                logger.exception("Failed to kick gated member")
                await self.db.log_moderation_action(
                    guild_id=guild_id,
                    action="join_gate_kick_failed",
                    actor_user_id=self.bot.user.id if self.bot.user else None,
                    target_user_id=user_id,
                    reason=reason,
                    metadata=metadata,
                )
                return "gated_kick_failed"

        try:
            await member.timeout(until=gate_until.isoformat(), reason=f"Join gated: {reason}")
            await self.db.log_moderation_action(
                guild_id=guild_id,
                action="join_gate_timeout",
                actor_user_id=self.bot.user.id if self.bot.user else None,
                target_user_id=user_id,
                reason=reason,
                metadata={**metadata, "gate_until": gate_until.isoformat()},
            )
            return "gated_timeout"
        except Exception:
            logger.exception("Failed to timeout gated member")
            await self.db.log_moderation_action(
                guild_id=guild_id,
                action="join_gate_timeout_failed",
                actor_user_id=self.bot.user.id if self.bot.user else None,
                target_user_id=user_id,
                reason=reason,
                metadata={**metadata, "gate_until": gate_until.isoformat()},
            )
            return "gated_timeout_failed"

    async def _dispatch_verified_welcome(self, member: fluxer.GuildMember) -> None:
        get_cog = getattr(self.bot, "get_cog", None)
        if not callable(get_cog):
            return

        welcome_cog = get_cog("WelcomeCog")
        if welcome_cog is None:
            return

        send_method = getattr(welcome_cog, "send_welcome_for_member", None)
        if send_method is None or not callable(send_method):
            return

        try:
            maybe_awaitable = send_method(member)
            if hasattr(maybe_awaitable, "__await__"):
                await cast(Any, maybe_awaitable)
        except Exception:
            logger.exception("Failed to dispatch verified welcome message")

    async def _assert_staff(self, ctx: fluxer.Message) -> bool:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return False

        try:
            if await is_staff_member(self.bot, ctx):
                return True
        except Exception:
            logger.exception("Failed to evaluate staff membership")

        await ctx.reply(embed=error_embed("Permission Denied", "This command is restricted to staff."))
        return False

    async def _deliver_totp_setup(
        self,
        ctx: fluxer.Message,
        secret: str,
        uri: str,
        success_title: str,
        success_description: str,
    ) -> None:
        setup_message = (
            "TOTP setup is ready. Add this secret to your authenticator app:\n"
            f"Secret: {secret}\n"
            f"Provisioning URI: {uri}\n"
            "Sensitive data warning: do not share this secret with anyone."
        )

        try:
            if hasattr(ctx.author, "send"):
                await ctx.author.send(setup_message)
                await ctx.reply(
                    embed=success_embed(
                        success_title,
                        success_description,
                    )
                )
                return
        except Exception:
            logger.exception("Failed to DM TOTP setup details")

        await ctx.reply(
            embed=error_embed(
                "DM Required",
                "Could not send setup details in DM. Enable direct messages and run /totpsetup again.",
            )
        )

    @Cog.listener(name="on_member_join")
    async def on_member_join(self, payload: Any) -> None:
        member = self._parse_member_payload(payload, self.bot)
        if member is None:
            return

        if member.user.bot or member.guild_id is None:
            return

        guild_id = int(member.guild_id)
        config = await self.config_cache.get(guild_id)
        gate_state = await self._get_effective_gate_state(guild_id)

        now = datetime.now(timezone.utc)
        account_created_at = self._snowflake_to_datetime(member.user.id)
        account_age_days = max(0.0, (now - account_created_at).total_seconds() / 86400.0)

        profile_score, has_avatar = self._estimate_profile_score(member)
        signal = self.risk_engine.evaluate_join(
            guild_id=guild_id,
            account_age_days=account_age_days,
            has_avatar=has_avatar,
            profile_score=profile_score,
            window_seconds=config.raid_monitor_window_seconds,
            join_rate_threshold=config.raid_join_rate_threshold,
        )

        threshold = float(config.raid_gate_threshold)
        suspicious = signal.risk_score >= threshold
        cautious = signal.risk_score >= max(0.5, threshold - 0.1)

        gate_active = bool(gate_state.get("gate_active"))
        action = "allow"
        gate_reason = str(gate_state.get("gate_reason") or "")

        if config.raid_detection_enabled and suspicious and not gate_active:
            gate_until = now + timedelta(seconds=config.gate_duration_seconds)
            gate_reason = (
                "Automated anti-raid trigger in closed testing. "
                f"Reason: {signal.explanation}. risk={signal.risk_score:.3f}"
            )
            await self.db.set_raid_gate_state(guild_id, True, gate_reason, gate_until.isoformat())
            gate_active = True

            await self._send_security_log(
                guild_id,
                "Anti-Raid Gate Enabled",
                (
                    "Join gate was enabled automatically in closed testing.\n"
                    f"Risk score: {signal.risk_score:.3f}\n"
                    f"Signals: {signal.explanation}\n"
                    f"Gate mode: {config.join_gate_mode}\n"
                    f"Gate until: {gate_until.isoformat()}"
                ),
                level="warning",
            )

        should_gate = gate_active or (config.raid_detection_enabled and cautious)

        if should_gate:
            if not gate_reason:
                gate_reason = (
                    "Precautionary join gating while staff review suspicious pattern "
                    f"(risk={signal.risk_score:.3f}, {signal.explanation})."
                )

            action = await self._gate_member(
                member=member,
                signal=signal,
                reason=gate_reason,
                verification_url=config.verification_url,
                gate_mode=config.join_gate_mode,
                gate_duration_seconds=config.gate_duration_seconds,
            )

            await self._send_security_log(
                guild_id,
                "Member Join Gated",
                (
                    f"Member: {mention_user(member.user.id)}\n"
                    f"Action: {action}\n"
                    f"Risk score: {signal.risk_score:.3f}\n"
                    f"Risk level: {signal.risk_level}\n"
                    f"Signals: {signal.explanation}\n"
                    "Policy: uncertain joins are gated first, then escalated to staff."
                ),
                level="warning",
            )

        await self.db.log_join_event(
            guild_id=guild_id,
            user_id=member.user.id,
            account_age_days=signal.account_age_days,
            has_avatar=signal.has_avatar,
            profile_score=signal.profile_score,
            join_rate=signal.join_rate_per_minute,
            young_account_ratio=signal.young_account_ratio,
            risk_score=signal.risk_score,
            risk_level=signal.risk_level,
            action=action,
            metadata={
                "explanation": signal.explanation,
                "gate_active": gate_active,
            },
        )

    @Cog.command(name="totpsetup")
    async def totp_setup(self, ctx: fluxer.Message) -> None:
        if not await self._assert_staff(ctx):
            return
        if ctx.guild_id is None:
            return

        secret, uri = await self.totp_auth.register_user(ctx.guild_id, ctx.author.id)
        self.totp_auth.invalidate_session(ctx.guild_id, ctx.author.id)
        await self._deliver_totp_setup(
            ctx,
            secret,
            uri,
            success_title="TOTP Setup Created",
            success_description=(
                "Setup details were sent to your direct messages. "
                "This is closed testing; report unexpected issues."
            ),
        )

    @Cog.command(name="totpreset")
    async def totp_reset(self, ctx: fluxer.Message) -> None:
        if not await self._assert_staff(ctx):
            return
        if ctx.guild_id is None:
            return

        secret, uri = await self.totp_auth.register_user(ctx.guild_id, ctx.author.id)
        self.totp_auth.invalidate_session(ctx.guild_id, ctx.author.id)
        await self._deliver_totp_setup(
            ctx,
            secret,
            uri,
            success_title="TOTP Reset Complete",
            success_description=(
                "Previous TOTP secret was invalidated and replaced. "
                "New setup details were sent to your direct messages."
            ),
        )

    @Cog.command(name="totpauth")
    async def totp_authenticate(self, ctx: fluxer.Message, code: str = "") -> None:
        if not await self._assert_staff(ctx):
            return
        if ctx.guild_id is None:
            return

        verified, reason = await self.totp_auth.verify_command_totp(
            ctx.guild_id,
            ctx.author.id,
            code.strip(),
            issue_grant=False,
        )
        if not verified:
            await ctx.reply(embed=error_embed("TOTP Failed", reason))
            return

        await ctx.reply(
            embed=success_embed(
                "TOTP Verified",
                "Privileged commands are now authorized for the next 30 days. Re-run /totpauth when this window expires.",
            )
        )

    @Cog.command(name="totpdisable")
    async def totp_disable(self, ctx: fluxer.Message, code: str = "") -> None:
        if not await self._assert_staff(ctx):
            return
        if ctx.guild_id is None:
            return

        verified, reason = await self.totp_auth.verify_command_totp(ctx.guild_id, ctx.author.id, code.strip())
        if not verified:
            await ctx.reply(embed=error_embed("TOTP Failed", reason))
            return

        await self.db.disable_totp_secret(ctx.guild_id, ctx.author.id)
        self.totp_auth.invalidate_session(ctx.guild_id, ctx.author.id)
        await ctx.reply(
            embed=warning_embed(
                "TOTP Disabled",
                "Your TOTP enrollment was disabled. Run /totpsetup to re-enable secure command execution.",
            )
        )

    @Cog.command(name="setverificationurl")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    @require_totp
    async def set_verification_url(self, ctx: fluxer.Message, url: str = "") -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return

        value = url.strip()
        normalized = value.lower()

        if normalized in {"", "none", "off", "disable", "disabled", "clear", "null"}:
            await self.db.update_guild_config(ctx.guild_id, verification_url=None)
            self.config_cache.invalidate(ctx.guild_id)
            await self.config_cache.refresh(ctx.guild_id)

            await ctx.reply(
                embed=success_embed(
                    "Verification URL Cleared",
                    "External verification link is disabled. Join gating will continue with manual staff review.",
                )
            )
            return

        if not value.startswith("http://") and not value.startswith("https://"):
            await ctx.reply(
                embed=error_embed(
                    "Invalid URL",
                    "Use a full http:// or https:// verification URL, or run /setverificationurl off to disable it.",
                )
            )
            return

        await self.db.update_guild_config(ctx.guild_id, verification_url=value)
        self.config_cache.invalidate(ctx.guild_id)
        await self.config_cache.refresh(ctx.guild_id)

        await ctx.reply(
            embed=success_embed(
                "Verification URL Updated",
                "New members can now be redirected to the configured verification site.",
            )
        )

    @Cog.command(name="setraidsettings")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    @require_totp
    async def set_raid_settings(
        self,
        ctx: fluxer.Message,
        threshold: float = 0.72,
        join_rate_threshold: int = 8,
        window_seconds: int = 90,
        gate_duration_seconds: int = 900,
        mode: str = "timeout",
    ) -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return

        mode = mode.strip().lower()
        if mode not in {"timeout", "kick"}:
            await ctx.reply(embed=error_embed("Invalid Mode", "Mode must be timeout or kick."))
            return

        try:
            await self.db.update_guild_config(
                ctx.guild_id,
                raid_gate_threshold=threshold,
                raid_join_rate_threshold=join_rate_threshold,
                raid_monitor_window_seconds=window_seconds,
                gate_duration_seconds=gate_duration_seconds,
                join_gate_mode=mode,
            )
        except ValueError as exc:
            await ctx.reply(embed=error_embed("Invalid Settings", str(exc)))
            return

        self.config_cache.invalidate(ctx.guild_id)
        await self.config_cache.refresh(ctx.guild_id)

        await ctx.reply(
            embed=success_embed(
                "Raid Settings Updated",
                (
                    f"threshold={threshold}, join_rate_threshold={join_rate_threshold}, "
                    f"window_seconds={window_seconds}, gate_duration_seconds={gate_duration_seconds}, mode={mode}"
                ),
            )
        )

    @Cog.command(name="setraiddetection")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    @require_totp
    async def set_raid_detection(self, ctx: fluxer.Message, state: str = "on") -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return

        normalized = state.strip().lower()
        if normalized in {"on", "enable", "enabled", "true", "1"}:
            enabled = True
        elif normalized in {"off", "disable", "disabled", "false", "0"}:
            enabled = False
        else:
            await ctx.reply(embed=error_embed("Invalid State", "Use /setraiddetection on or /setraiddetection off."))
            return

        await self.db.update_guild_config(ctx.guild_id, raid_detection_enabled=enabled)
        self.config_cache.invalidate(ctx.guild_id)
        await self.config_cache.refresh(ctx.guild_id)

        await ctx.reply(
            embed=success_embed(
                "Raid Detection Updated",
                f"Automated anti-raid detection is now {'enabled' if enabled else 'disabled'}.",
            )
        )

    @Cog.command(name="raidgate")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    @require_totp
    async def raid_gate(self, ctx: fluxer.Message, action: str = "status", duration_seconds: int = 900) -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return

        chosen = action.strip().lower()
        guild_id = ctx.guild_id

        if chosen in {"status", "state"}:
            state = await self._get_effective_gate_state(guild_id)
            await ctx.reply(
                embed=info_embed(
                    "Raid Gate Status",
                    (
                        f"active={state.get('gate_active')}\n"
                        f"reason={state.get('gate_reason')}\n"
                        f"until={state.get('gate_until')}"
                    ),
                )
            )
            return

        if chosen in {"off", "disable", "close"}:
            await self.db.set_raid_gate_state(guild_id, False, "Manual disable by staff", None)
            await ctx.reply(embed=success_embed("Raid Gate Disabled", "Join gate is now disabled."))
            await self._send_security_log(
                guild_id,
                "Raid Gate Disabled",
                f"Disabled manually by {mention_user(ctx.author.id)}.",
                level="info",
            )
            return

        if chosen in {"on", "enable", "open"}:
            duration_seconds = max(60, min(duration_seconds, 86400))
            until = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
            reason = f"Manual gate enabled by {ctx.author.id}"
            await self.db.set_raid_gate_state(guild_id, True, reason, until.isoformat())
            await ctx.reply(
                embed=warning_embed(
                    "Raid Gate Enabled",
                    f"Join gate is active until {until.isoformat()}.",
                )
            )
            await self._send_security_log(
                guild_id,
                "Raid Gate Enabled",
                f"Enabled manually by {mention_user(ctx.author.id)} until {until.isoformat()}.",
                level="warning",
            )
            return

        await ctx.reply(embed=error_embed("Invalid Action", "Use /raidgate on, /raidgate off, or /raidgate status."))

    @Cog.command(name="verifyjoin")
    @has_permission(fluxer.Permissions.MODERATE_MEMBERS)
    @require_totp
    async def verify_join(self, ctx: fluxer.Message, target: str = "") -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return

        target_id = await resolve_target_user_id(ctx, target)
        if target_id is None:
            await ctx.reply(embed=error_embed("Missing Target", "Provide a user ID/mention or reply to the member."))
            return

        status = await self.db.get_verification_status(ctx.guild_id, target_id)
        if status is None or status.get("status") != "pending":
            await ctx.reply(embed=warning_embed("No Pending Verification", "That member is not currently in the pending queue."))
            return

        await self.db.upsert_verification_member(
            guild_id=ctx.guild_id,
            user_id=target_id,
            status="verified",
            risk_score=float(status.get("risk_score", 0.0)),
            verification_url=status.get("verification_url"),
            reason="Manually verified by staff",
            verified_by_user_id=ctx.author.id,
        )

        member: fluxer.GuildMember | None = None
        try:
            guild = await self.bot.fetch_guild(str(ctx.guild_id))
            member = await guild.fetch_member(target_id)
            await member.timeout(until=None, reason=f"Verification approved by {ctx.author.id}")
        except Exception:
            logger.debug("Member timeout clear skipped (member may be absent or not timed out)", exc_info=True)

        await self.db.log_moderation_action(
            guild_id=ctx.guild_id,
            action="verification_approved",
            actor_user_id=ctx.author.id,
            target_user_id=target_id,
            reason="Join verification approved",
            channel_id=ctx.channel_id,
            message_id=ctx.id,
        )

        if member is not None:
            await self._dispatch_verified_welcome(member)

        await ctx.reply(embed=success_embed("Verification Approved", f"{mention_user(target_id)} is now approved."))
        await self._send_security_log(
            ctx.guild_id,
            "Verification Approved",
            f"{mention_user(ctx.author.id)} approved {mention_user(target_id)} for normal access.",
            level="info",
        )

    @Cog.command(name="rejectjoin")
    @has_permission(fluxer.Permissions.KICK_MEMBERS)
    @require_totp
    async def reject_join(self, ctx: fluxer.Message, target: str = "", *, reason: str = "No reason provided") -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return

        target_id = await resolve_target_user_id(ctx, target)
        if target_id is None:
            await ctx.reply(embed=error_embed("Missing Target", "Provide a user ID/mention or reply to the member."))
            return

        status = await self.db.get_verification_status(ctx.guild_id, target_id)
        risk_score = float(status["risk_score"]) if status else 0.0
        verification_url = status["verification_url"] if status else None

        await self.db.upsert_verification_member(
            guild_id=ctx.guild_id,
            user_id=target_id,
            status="rejected",
            risk_score=risk_score,
            verification_url=verification_url,
            reason=reason,
            verified_by_user_id=ctx.author.id,
        )

        kicked = False
        try:
            guild = await self.bot.fetch_guild(str(ctx.guild_id))
            member = await guild.fetch_member(target_id)
            await member.kick(reason=f"Join rejected: {reason}")
            kicked = True
        except Exception:
            logger.exception("Failed to kick rejected member")

        await self.db.log_moderation_action(
            guild_id=ctx.guild_id,
            action="verification_rejected",
            actor_user_id=ctx.author.id,
            target_user_id=target_id,
            reason=reason,
            channel_id=ctx.channel_id,
            message_id=ctx.id,
            metadata={"kick_applied": kicked},
        )

        if kicked:
            await ctx.reply(embed=warning_embed("Verification Rejected", f"{mention_user(target_id)} was removed."))
        else:
            await ctx.reply(
                embed=warning_embed(
                    "Verification Marked Rejected",
                    "Status was updated, but the member could not be removed. Check bot permissions.",
                )
            )

    @Cog.command(name="pendingverifications")
    @has_permission(fluxer.Permissions.MODERATE_MEMBERS)
    @require_totp
    async def pending_verifications(self, ctx: fluxer.Message, limit: int = 10) -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return

        entries = await self.db.list_pending_verifications(ctx.guild_id, limit=limit)
        if not entries:
            await ctx.reply(embed=info_embed("Pending Verifications", "No pending members in the queue."))
            return

        lines: list[str] = []
        for entry in entries:
            lines.append(
                (
                    f"- {mention_user(int(entry['user_id']))} | risk={entry['risk_score']:.3f} "
                    f"| reason={entry['reason']}"
                )
            )

        await ctx.reply(embed=info_embed("Pending Verifications", "\n".join(lines)))

    @Cog.command(name="raidsnapshot")
    @has_permission(fluxer.Permissions.MODERATE_MEMBERS)
    @require_totp
    async def raid_snapshot(self, ctx: fluxer.Message, limit: int = 10) -> None:
        if ctx.guild_id is None:
            await ctx.reply(embed=error_embed("Server Only", "This command can only be used in a server."))
            return

        entries = await self.db.get_recent_join_events(ctx.guild_id, limit=limit)
        if not entries:
            await ctx.reply(embed=info_embed("Raid Snapshot", "No join events recorded yet."))
            return

        lines: list[str] = []
        for entry in entries:
            lines.append(
                (
                    f"- {mention_user(int(entry['user_id']))} | action={entry['action']} "
                    f"| risk={entry['risk_score']:.3f} | level={entry['risk_level']}"
                )
            )

        await ctx.reply(
            embed=info_embed(
                "Raid Snapshot",
                "Closed testing telemetry. Handle these records as sensitive moderation data.\n" + "\n".join(lines),
            )
        )


async def setup(bot: fluxer.Bot) -> None:
    await bot.add_cog(SecurityCog(bot))
