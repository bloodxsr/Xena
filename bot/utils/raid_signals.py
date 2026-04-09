from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass


@dataclass(slots=True)
class JoinRiskSignal:
    account_age_days: float
    has_avatar: bool
    profile_score: float
    join_rate_per_minute: float
    young_account_ratio: float
    risk_score: float
    risk_level: str
    explanation: str


@dataclass(slots=True)
class _GuildJoinState:
    join_timestamps: deque[float]
    young_flags: deque[tuple[float, int]]


class RaidRiskEngine:
    """Online logistic-style scorer for suspicious member joins."""

    def __init__(self) -> None:
        self._guild_state: dict[int, _GuildJoinState] = {}

    def evaluate_join(
        self,
        guild_id: int,
        account_age_days: float,
        has_avatar: bool,
        profile_score: float,
        window_seconds: int,
        join_rate_threshold: int,
    ) -> JoinRiskSignal:
        now = time.time()
        state = self._guild_state.setdefault(
            guild_id,
            _GuildJoinState(join_timestamps=deque(), young_flags=deque()),
        )

        state.join_timestamps.append(now)
        while state.join_timestamps and now - state.join_timestamps[0] > window_seconds:
            state.join_timestamps.popleft()

        is_young = 1 if account_age_days <= 7.0 else 0
        state.young_flags.append((now, is_young))
        while state.young_flags and now - state.young_flags[0][0] > window_seconds:
            state.young_flags.popleft()

        window_minutes = max(1.0, window_seconds / 60.0)
        join_rate_per_minute = len(state.join_timestamps) / window_minutes

        if state.young_flags:
            young_account_ratio = sum(flag for _, flag in state.young_flags) / len(state.young_flags)
        else:
            young_account_ratio = 0.0

        burst_feature = min(1.5, join_rate_per_minute / max(1.0, float(join_rate_threshold)))
        new_account_feature = max(0.0, min(1.0, (14.0 - account_age_days) / 14.0))
        profile_gap_feature = 1.0 - max(0.0, min(1.0, profile_score))
        coordinated_feature = max(0.0, min(1.0, young_account_ratio))
        avatar_feature = 0.0 if has_avatar else 1.0

        linear = (
            -1.35
            + 1.95 * burst_feature
            + 1.35 * new_account_feature
            + 1.10 * coordinated_feature
            + 0.90 * profile_gap_feature
            + 0.45 * avatar_feature
        )
        risk_score = 1.0 / (1.0 + math.exp(-linear))

        if risk_score >= 0.82:
            risk_level = "high"
        elif risk_score >= 0.60:
            risk_level = "medium"
        else:
            risk_level = "low"

        explanation_parts: list[str] = []
        if burst_feature >= 1.0:
            explanation_parts.append("join burst above baseline")
        if new_account_feature >= 0.7:
            explanation_parts.append("very new account")
        if coordinated_feature >= 0.6:
            explanation_parts.append("cluster of young accounts")
        if profile_gap_feature >= 0.6:
            explanation_parts.append("low profile completeness")
        if not explanation_parts:
            explanation_parts.append("signals within expected range")

        explanation = ", ".join(explanation_parts)

        return JoinRiskSignal(
            account_age_days=account_age_days,
            has_avatar=has_avatar,
            profile_score=max(0.0, min(1.0, profile_score)),
            join_rate_per_minute=join_rate_per_minute,
            young_account_ratio=young_account_ratio,
            risk_score=risk_score,
            risk_level=risk_level,
            explanation=explanation,
        )
