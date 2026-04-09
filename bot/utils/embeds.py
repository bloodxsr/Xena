from __future__ import annotations

from typing import Iterable

import fluxer

COLOR_INFO = 0x4A90E2
COLOR_SUCCESS = 0x3CB371
COLOR_WARNING = 0xF1A208
COLOR_ERROR = 0xE74C3C
COLOR_NEUTRAL = 0x5B8DEF


def _clean_text(value: str, fallback: str) -> str:
    cleaned = value.strip()
    return cleaned if cleaned else fallback


def _styled_embed(title: str, description: str, color: int, footer: str) -> fluxer.Embed:
    embed = fluxer.Embed(
        title=_clean_text(title, "Update"),
        description=_clean_text(description, "No additional details were provided."),
        color=color,
    )
    embed.set_footer(text=footer)
    return embed


def _quote_block(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    if not lines:
        return ">"
    return "\n".join(f"> {line}" if line else ">" for line in lines)


def info_embed(title: str, description: str) -> fluxer.Embed:
    return _styled_embed(
        title=f"Info: {_clean_text(title, 'Update')}",
        description=_clean_text(description, "No additional details were provided."),
        color=COLOR_INFO,
        footer="Fluxer Bot | Need commands? Use /help",
    )


def success_embed(title: str, description: str) -> fluxer.Embed:
    return _styled_embed(
        title=f"Success: {_clean_text(title, 'Completed')}",
        description=_clean_text(description, "Action completed successfully."),
        color=COLOR_SUCCESS,
        footer="Fluxer Bot | Completed",
    )


def warning_embed(title: str, description: str) -> fluxer.Embed:
    return _styled_embed(
        title=f"Warning: {_clean_text(title, 'Attention')}",
        description=_clean_text(description, "Please review this notice."),
        color=COLOR_WARNING,
        footer="Fluxer Bot | Please review",
    )


def error_embed(title: str, description: str) -> fluxer.Embed:
    return _styled_embed(
        title=f"Error: {_clean_text(title, 'Something went wrong')}",
        description=_clean_text(description, "An unexpected error occurred."),
        color=COLOR_ERROR,
        footer="Fluxer Bot | Try again or contact staff",
    )


def moderation_log_embed(
    action: str,
    target_user_id: int | None,
    actor_user_id: int | None,
    reason: str | None,
    details: str | None = None,
) -> fluxer.Embed:
    clean_action = _clean_text(action, "Unknown")
    embed = fluxer.Embed(
        title="Moderation Action Report",
        description=(
            "**Action Summary**\n"
            f"{_clean_text(details or '', 'No extra details provided.')}"
        ),
        color=COLOR_NEUTRAL,
    )
    embed.add_field(name="Action Type", value=clean_action, inline=True)
    if target_user_id is not None:
        embed.add_field(name="Target User", value=f"<@{target_user_id}>", inline=True)
    if actor_user_id is not None:
        embed.add_field(name="Moderator", value=f"<@{actor_user_id}>", inline=True)
    embed.add_field(name="Reason", value=_clean_text(reason or "", "No reason provided"), inline=False)
    embed.set_footer(text="Fluxer Bot | Moderation Audit Trail")
    return embed


def warnings_status_embed(user_id: int, count: int, max_warnings: int) -> fluxer.Embed:
    color = COLOR_WARNING if count < max_warnings else COLOR_ERROR
    ratio = 0.0 if max_warnings <= 0 else min(1.0, max(0.0, count / max_warnings))
    filled_slots = int(round(ratio * 10))
    progress_bar = f"[{'#' * filled_slots}{'-' * (10 - filled_slots)}]"
    status_text = "Threshold reached" if count >= max_warnings else "Within threshold"
    embed = fluxer.Embed(
        title="Warning Status Overview",
        description=(
            f"**Member**: <@{user_id}>\n"
            f"**Warnings**: {count}/{max_warnings}\n"
            f"**Status**: {status_text}\n"
            f"**Progress**: {progress_bar}"
        ),
        color=color,
    )
    if count >= max_warnings - 1:
        embed.add_field(name="Escalation", value="Next violation triggers a kick.", inline=False)
    embed.set_footer(text="Fluxer Bot | Warning Tracker")
    return embed


def blacklist_page_embed(words: Iterable[str], page: int, total_pages: int, total_words: int) -> fluxer.Embed:
    page_words = list(words)
    body = "\n".join(f"{index}. {word}" for index, word in enumerate(page_words, start=1))
    body = body if body else "No words found on this page."
    embed = fluxer.Embed(
        title="Blacklist Manager",
        description=f"Configured filtered terms:\n\n{body}",
        color=COLOR_INFO,
    )
    embed.add_field(name="Page", value=f"{page}/{total_pages}", inline=True)
    embed.add_field(name="Entries This Page", value=str(len(page_words)), inline=True)
    embed.add_field(name="Total Words", value=str(total_words), inline=True)
    embed.set_footer(text="Page navigation: /viewbadwordsnext and /viewbadwordsprev")
    return embed


def welcome_embed(member_name: str, guild_name: str, links_text: str) -> fluxer.Embed:
    embed = fluxer.Embed(
        title=f"Welcome to {guild_name}",
        description=(
            f"Hello **{member_name}**!\n\n"
            "**Start Here**\n"
            f"{links_text}\n\n"
            "**Community Notes**\n"
            "Read the rules, ask questions when needed, and enjoy your stay."
        ),
        color=COLOR_SUCCESS,
    )
    embed.set_footer(text="Need help? Run /help or /helpmenu for command categories.")
    return embed


def ai_response_embed(title: str, response_text: str) -> fluxer.Embed:
    clean_title = _clean_text(title, "Response")
    clean_response = _clean_text(response_text, "No response generated.")
    embed = fluxer.Embed(
        title=f"AI Response: {clean_title}",
        description=_quote_block(clean_response),
        color=COLOR_NEUTRAL,
    )
    embed.add_field(name="Model", value="Gemini 2.5", inline=True)
    embed.set_footer(text="Fluxer Bot | Powered by Google Gemini")
    return embed
