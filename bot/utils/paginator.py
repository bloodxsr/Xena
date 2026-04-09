from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class PaginationSession:
    user_id: int
    items: list[str]
    page_size: int
    page_index: int = 0
    last_access: float = field(default_factory=time.time)

    @property
    def total_pages(self) -> int:
        if not self.items:
            return 1
        return (len(self.items) + self.page_size - 1) // self.page_size

    @property
    def page_number(self) -> int:
        return self.page_index + 1

    def get_page_items(self) -> list[str]:
        start = self.page_index * self.page_size
        end = start + self.page_size
        return self.items[start:end]

    def advance(self) -> None:
        if self.page_index < self.total_pages - 1:
            self.page_index += 1
        self.last_access = time.time()

    def rewind(self) -> None:
        if self.page_index > 0:
            self.page_index -= 1
        self.last_access = time.time()


class PaginatorManager:
    def __init__(self, page_size: int = 20, session_timeout: int = 300) -> None:
        self.page_size = page_size
        self.session_timeout = session_timeout
        self._sessions: dict[int, PaginationSession] = {}

    def cleanup_expired(self) -> None:
        now = time.time()
        expired = [
            user_id
            for user_id, session in self._sessions.items()
            if now - session.last_access >= self.session_timeout
        ]
        for user_id in expired:
            self._sessions.pop(user_id, None)

    def start(self, user_id: int, items: list[str]) -> PaginationSession:
        self.cleanup_expired()
        session = PaginationSession(user_id=user_id, items=items, page_size=self.page_size)
        self._sessions[user_id] = session
        return session

    def get(self, user_id: int) -> PaginationSession | None:
        self.cleanup_expired()
        session = self._sessions.get(user_id)
        if session is not None:
            session.last_access = time.time()
        return session

    def next_page(self, user_id: int) -> PaginationSession | None:
        session = self.get(user_id)
        if session is None:
            return None
        session.advance()
        return session

    def previous_page(self, user_id: int) -> PaginationSession | None:
        session = self.get(user_id)
        if session is None:
            return None
        session.rewind()
        return session

    def clear(self, user_id: int) -> None:
        self._sessions.pop(user_id, None)
