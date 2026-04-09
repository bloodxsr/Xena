from __future__ import annotations

import asyncio
import importlib
import logging
import random
import time
from typing import Any

import fluxer
from fluxer import Cog

from utils.checks import has_permission
from utils.embeds import ai_response_embed, error_embed, info_embed, warning_embed

try:
    genai = importlib.import_module("google.generativeai")
except Exception:  # pragma: no cover - import failure handled gracefully
    genai = None

logger = logging.getLogger(__name__)


class AICog(Cog):
    def __init__(self, bot: fluxer.Bot) -> None:
        super().__init__(bot)
        self.api_key: str = getattr(bot, "google_api_key", "")
        self.model_name = getattr(bot, "ai_model_name", "gemini-2.5-flash")
        self.rate_limit_seconds = int(getattr(bot, "ai_rate_limit_seconds", 5))
        self.request_timeout_seconds = int(getattr(bot, "ai_timeout_seconds", 30))
        self.max_response_length = int(getattr(bot, "ai_max_response_length", 1500))
        self.max_question_length = int(getattr(bot, "ai_max_question_length", 1500))
        self._last_used: dict[int, float] = {}
        self._model: Any | None = None

        self._configure_model()

    def _configure_model(self) -> None:
        if not self.api_key or genai is None:
            return

        try:
            genai.configure(api_key=self.api_key)
            self._model = genai.GenerativeModel(self.model_name)
            logger.info("Gemini AI model configured: %s", self.model_name)
        except Exception:
            logger.exception("Failed to initialize Gemini client")
            self._model = None

    def _check_rate_limit(self, user_id: int) -> tuple[bool, int]:
        now = time.time()
        previous = self._last_used.get(user_id)
        if previous is None:
            self._last_used[user_id] = now
            return True, 0

        elapsed = now - previous
        if elapsed >= self.rate_limit_seconds:
            self._last_used[user_id] = now
            return True, 0

        remaining = int(self.rate_limit_seconds - elapsed) + 1
        return False, remaining

    async def _generate(self, prompt: str) -> str:
        model = self._model
        if model is None:
            return "AI service is not configured. Add a valid key to google.txt or GOOGLE_API_KEY."

        def _sync_generate() -> str:
            response = model.generate_content(prompt)
            text = getattr(response, "text", "")
            if text:
                return str(text)

            candidates = getattr(response, "candidates", None) or []
            if candidates:
                parts = []
                for candidate in candidates:
                    content = getattr(candidate, "content", None)
                    if not content:
                        continue
                    for part in getattr(content, "parts", []) or []:
                        if getattr(part, "text", None):
                            parts.append(part.text)
                if parts:
                    return "\n".join(parts)

            return "I could not generate a response. Please try rephrasing your prompt."

        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(_sync_generate),
                timeout=self.request_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return "The AI request timed out. Please try again with a shorter prompt."
        except Exception:
            logger.exception("Gemini generation failed")
            return "The AI service ran into an error while generating a reply."

        clean = text.strip()
        if len(clean) > self.max_response_length:
            clean = clean[: self.max_response_length - 3].rstrip() + "..."
        return clean

    @Cog.command(name="ask")
    async def ask(self, ctx: fluxer.Message, *, question: str = "") -> None:
        question = question.strip()
        if len(question) < 3:
            await ctx.reply(embed=error_embed("Invalid Question", "Please provide a question with at least 3 characters."))
            return
        if len(question) > self.max_question_length:
            await ctx.reply(
                embed=error_embed(
                    "Question Too Long",
                    f"Question length must not exceed {self.max_question_length} characters.",
                )
            )
            return

        allowed, remaining = self._check_rate_limit(ctx.author.id)
        if not allowed:
            await ctx.reply(
                embed=warning_embed(
                    "Rate Limited",
                    f"Please wait {remaining} second(s) before using AI commands again.",
                )
            )
            return

        if ctx.channel is not None:
            try:
                await ctx.channel.trigger_typing()
            except Exception:
                logger.debug("Could not trigger typing indicator", exc_info=True)

        prompt = (
            "You are a concise and helpful assistant for a community moderation bot. "
            "Answer clearly and avoid unsafe content.\n\n"
            f"User question: {question}"
        )

        answer = await self._generate(prompt)
        await ctx.reply(embed=ai_response_embed("AI Response", answer))

    @Cog.command(name="joke")
    async def joke(self, ctx: fluxer.Message) -> None:
        allowed, remaining = self._check_rate_limit(ctx.author.id)
        if not allowed:
            await ctx.reply(
                embed=warning_embed(
                    "Rate Limited",
                    f"Please wait {remaining} second(s) before using AI commands again.",
                )
            )
            return

        prompt = random.choice(
            [
                "Tell a short, clean, family-friendly joke.",
                "Share a clever programming joke in 1-3 lines.",
                "Tell a light community-safe joke with no offensive language.",
            ]
        )

        joke = await self._generate(prompt)
        await ctx.reply(embed=ai_response_embed("Here is a joke", joke))

    @Cog.command(name="aistatus")
    @has_permission(fluxer.Permissions.ADMINISTRATOR)
    async def ai_status(self, ctx: fluxer.Message) -> None:
        configured = bool(self._model)
        status = "online" if configured else "offline"
        active_cooldowns = sum(
            1
            for _, timestamp in self._last_used.items()
            if (time.time() - timestamp) < self.rate_limit_seconds
        )
        details = (
            f"status: {status}\n"
            f"model: {self.model_name}\n"
            f"rate_limit_seconds: {self.rate_limit_seconds}\n"
            f"active_cooldowns: {active_cooldowns}"
        )
        await ctx.reply(embed=info_embed("AI Service Status", details))


async def setup(bot: fluxer.Bot) -> None:
    await bot.add_cog(AICog(bot))
