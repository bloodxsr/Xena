from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import fluxer
from dotenv import load_dotenv

from database.db import Database
from utils.autodelete import AutoDeleteManager, parse_auto_delete_commands
from utils.checks import ConfigCache
from utils.totp_auth import TotpAuthManager
from utils.word_store import WordStore

BASE_DIR = Path(__file__).resolve().parent
TOKEN_PATH = BASE_DIR / "token.txt"
GOOGLE_KEY_PATH = BASE_DIR / "google.txt"
WORDS_PATH = BASE_DIR / "words.py"
DB_PATH = BASE_DIR / "warnings.db"


def to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def load_token() -> str:
    token = (
        os.getenv("FLUXER_BOT_TOKEN")
        or os.getenv("BOT_TOKEN")
        or os.getenv("TOKEN")
        or read_text_file(TOKEN_PATH)
    )
    return token.strip()


def load_google_key() -> str:
    api_key = (
        os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or read_text_file(GOOGLE_KEY_PATH)
    )
    return api_key.strip()


def parse_prefixes() -> list[str]:
    raw = os.getenv("BOT_PREFIXES", "/,!")
    prefixes = [prefix.strip() for prefix in raw.split(",") if prefix.strip()]
    return prefixes or ["/"]


class CommunityBot(fluxer.Bot):
    def __init__(self) -> None:
        intents = (
            fluxer.Intents.default()
            | fluxer.Intents.MESSAGE_CONTENT
            | fluxer.Intents.GUILD_MEMBERS
            | fluxer.Intents.GUILD_MODERATION
        )

        super().__init__(command_prefix=parse_prefixes(), intents=intents)

        self.db = Database(DB_PATH)
        self.config_cache = ConfigCache(self.db, ttl_seconds=45)
        self.word_store = WordStore(WORDS_PATH)
        self.totp_auth = TotpAuthManager(
            self.db,
            issuer_name=os.getenv("TOTP_ISSUER", "FluxerBot"),
        )

        self.auto_delete = AutoDeleteManager(
            enabled=to_bool(os.getenv("AUTO_DELETE_ENABLED"), default=True),
            delay_seconds=int(os.getenv("AUTO_DELETE_DELAY_SECONDS", "10")),
            command_names=parse_auto_delete_commands(os.getenv("AUTO_DELETE_COMMANDS")),
        )

        self.max_warnings = int(os.getenv("MAX_WARNINGS", "4"))

        self.google_api_key = load_google_key()
        self.ai_model_name = os.getenv("AI_MODEL_NAME", "gemini-2.5-flash")
        self.ai_rate_limit_seconds = int(os.getenv("AI_RATE_LIMIT_SECONDS", "5"))
        self.ai_timeout_seconds = int(os.getenv("AI_TIMEOUT_SECONDS", "30"))
        self.ai_max_response_length = int(os.getenv("AI_MAX_RESPONSE_LENGTH", "1500"))
        self.ai_max_question_length = int(os.getenv("AI_MAX_QUESTION_LENGTH", "1500"))

        self.about_text = os.getenv(
            "ABOUT_TEXT",
            "Welcome to our community. Keep chats constructive, respectful, and fun.",
        )
        self.perks_text = os.getenv(
            "PERKS_TEXT",
            "Be active to unlock higher-trust roles, private channels, and event access.",
        )

        self.extensions_to_load = [
            "cogs.moderation",
            "cogs.admin",
            "cogs.ai",
            "cogs.welcome",
            "cogs.security",
        ]

        @self.event
        async def on_ready() -> None:
            logging.getLogger(__name__).info("Bot connected as %s", self.user)

    async def setup_hook(self) -> None:
        await self.db.initialize()
        await self.word_store.load()

        for extension in self.extensions_to_load:
            try:
                await self.load_extension(extension)
                logging.getLogger(__name__).info("Loaded extension %s", extension)
            except Exception:
                logging.getLogger(__name__).exception("Failed to load extension %s", extension)


async def maybe_start_keep_alive() -> None:
    if not to_bool(os.getenv("ENABLE_UPTIME_SERVER"), default=False):
        return

    try:
        from keep_alive import run_keep_alive

        host = os.getenv("UPTIME_HOST", "0.0.0.0")
        port = int(os.getenv("UPTIME_PORT", "8080"))
        run_keep_alive(host=host, port=port)
        logging.getLogger(__name__).info("Uptime server started on %s:%s", host, port)
    except Exception:
        logging.getLogger(__name__).exception("Failed to start uptime server")


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    token = load_token()
    if not token:
        raise RuntimeError(
            "Missing bot token. Put it in bot/token.txt or set FLUXER_BOT_TOKEN environment variable."
        )

    bot = CommunityBot()
    asyncio.run(maybe_start_keep_alive())
    bot.run(token)


if __name__ == "__main__":
    main()
