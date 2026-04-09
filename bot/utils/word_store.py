from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path


class WordStore:
    def __init__(self, words_file: str | Path) -> None:
        self.words_file = Path(words_file)
        self._words: set[str] = set()
        self._pattern: re.Pattern[str] | None = None
        self._lock = asyncio.Lock()

    @property
    def words(self) -> set[str]:
        return set(self._words)

    def as_sorted_list(self) -> list[str]:
        return sorted(self._words)

    async def load(self) -> set[str]:
        async with self._lock:
            self._words = await asyncio.to_thread(self._read_words_file)
            self._compile_pattern()
            return self.words

    async def reload(self) -> set[str]:
        return await self.load()

    async def add_word(self, word: str) -> bool:
        normalized = self._normalize_word(word)
        if not normalized:
            return False

        async with self._lock:
            if normalized in self._words:
                return False
            self._words.add(normalized)
            self._compile_pattern()
            await asyncio.to_thread(self._write_words_file)
            return True

    async def remove_word(self, word: str) -> bool:
        normalized = self._normalize_word(word)
        if not normalized:
            return False

        async with self._lock:
            if normalized not in self._words:
                return False
            self._words.remove(normalized)
            self._compile_pattern()
            await asyncio.to_thread(self._write_words_file)
            return True

    async def replace_words(self, words: list[str]) -> None:
        cleaned = {self._normalize_word(word) for word in words}
        cleaned.discard("")
        async with self._lock:
            self._words = cleaned
            self._compile_pattern()
            await asyncio.to_thread(self._write_words_file)

    def matches(self, text: str) -> bool:
        if not text or not self._words:
            return False
        if self._pattern is not None:
            return self._pattern.search(text) is not None
        lowered = text.lower()
        return any(word in lowered for word in self._words)

    def _compile_pattern(self) -> None:
        if not self._words:
            self._pattern = None
            return

        escaped = [re.escape(word) for word in sorted(self._words, key=len, reverse=True)]
        combined = "|".join(escaped)
        self._pattern = re.compile(combined, re.IGNORECASE)

    def _read_words_file(self) -> set[str]:
        if not self.words_file.exists():
            self.words_file.parent.mkdir(parents=True, exist_ok=True)
            self.words_file.write_text("blat = []\n", encoding="utf-8")
            return set()

        try:
            content = self.words_file.read_text(encoding="utf-8")
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == "blat":
                            if isinstance(node.value, (ast.List, ast.Tuple)):
                                values: set[str] = set()
                                for item in node.value.elts:
                                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                                        normalized = self._normalize_word(item.value)
                                        if normalized:
                                            values.add(normalized)
                                return values
        except (OSError, SyntaxError, ValueError):
            pass

        return set()

    def _write_words_file(self) -> None:
        payload = sorted(self._words)
        content = (
            "# Blacklisted words and phrases used by automated moderation.\n"
            "# Use /addbadword, /removebadword, and /reloadwords commands to manage this list.\n"
            f"blat = {payload!r}\n"
        )
        self.words_file.parent.mkdir(parents=True, exist_ok=True)
        self.words_file.write_text(content, encoding="utf-8")

    @staticmethod
    def _normalize_word(word: str) -> str:
        return word.strip().lower()
