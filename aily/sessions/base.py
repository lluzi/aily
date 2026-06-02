"""Base classes for Aily Three-Mind System.

Provides common scheduling infrastructure and circuit breaker pattern
for failure handling across all minds.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from aily.llm.client import LLMClient

logger = logging.getLogger(__name__)


class SessionState(Enum):
    """State of a scheduled mind session."""

    IDLE = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CIRCUIT_OPEN = auto()  # Circuit breaker tripped


@dataclass
class SessionResult:
    """Result of a scheduled mind session."""

    session_id: str
    mind_name: str
    state: SessionState
    started_at: datetime | None = None
    completed_at: datetime | None = None
    proposals_generated: int = 0
    proposals_delivered: int = 0
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CircuitState:
    """Internal state for circuit breaker."""

    failures: int = 0
    last_failure: datetime | None = None
    state: str = "closed"  # closed, open, half-open
    recovery_attempts: int = 0


class CircuitBreakerMixin:
    """Circuit breaker pattern for mind failure handling.

    Prevents cascading failures by disabling a mind after consecutive
    failures. Allows manual recovery via Feishu commands.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: timedelta = timedelta(minutes=30),
    ) -> None:
        self._circuit = CircuitState()
        self._circuit_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._lock = asyncio.Lock()

    async def can_execute(self) -> bool:
        """Check if execution is allowed (circuit closed or half-open)."""
        async with self._lock:
            if self._circuit.state == "closed":
                return True

            if self._circuit.state == "open":
                # Check if recovery timeout has passed
                if self._circuit.last_failure:
                    elapsed = datetime.now(timezone.utc) - self._circuit.last_failure
                    if elapsed >= self._recovery_timeout:
                        self._circuit.state = "half-open"
                        self._circuit.recovery_attempts = 0
                        logger.info("Circuit breaker entering half-open state for recovery")
                        return True
                return False

            if self._circuit.state == "half-open":
                # Allow one test execution
                return True

            return False

    async def record_success(self) -> None:
        """Record successful execution, reset circuit if needed."""
        async with self._lock:
            if self._circuit.state == "half-open":
                self._circuit.state = "closed"
                self._circuit.failures = 0
                self._circuit.recovery_attempts = 0
                logger.info("Circuit breaker closed after successful recovery")
            else:
                self._circuit.failures = 0

    async def record_failure(self) -> bool:
        """Record failed execution, trip circuit if threshold reached.

        Returns:
            True if circuit is now open (tripped), False otherwise.
        """
        async with self._lock:
            self._circuit.failures += 1
            self._circuit.last_failure = datetime.now(timezone.utc)

            if self._circuit.state == "half-open":
                # Failed recovery attempt, go back to open
                self._circuit.state = "open"
                self._circuit.recovery_attempts += 1
                logger.warning(
                    "Circuit breaker recovery failed (attempt %d), returning to open",
                    self._circuit.recovery_attempts,
                )
                return True

            if self._circuit.failures >= self._circuit_threshold:
                self._circuit.state = "open"
                logger.error(
                    "Circuit breaker tripped after %d consecutive failures",
                    self._circuit.failures,
                )
                return True

            return False

    def get_circuit_status(self) -> dict[str, Any]:
        """Get current circuit breaker status for monitoring."""
        return {
            "state": self._circuit.state,
            "failures": self._circuit.failures,
            "threshold": self._circuit_threshold,
            "last_failure": self._circuit.last_failure.isoformat() if self._circuit.last_failure else None,
            "recovery_attempts": self._circuit.recovery_attempts,
        }

    async def reset(self) -> None:
        """Manually reset circuit breaker (via Feishu command)."""
        async with self._lock:
            self._circuit.state = "closed"
            self._circuit.failures = 0
            self._circuit.recovery_attempts = 0
            logger.info("Circuit breaker manually reset")

    # Property accessors for backward compatibility and testing
    @property
    def _failure_count(self) -> int:
        return self._circuit.failures

    @_failure_count.setter
    def _failure_count(self, value: int) -> None:
        self._circuit.failures = value

    @property
    def _state(self) -> str:
        return self._circuit.state

    @_state.setter
    def _state(self, value: str) -> None:
        self._circuit.state = value

    @property
    def _failure_threshold(self) -> int:
        return self._circuit_threshold

    @_failure_threshold.setter
    def _failure_threshold(self, value: int) -> None:
        self._circuit_threshold = value


class BaseMindScheduler(ABC):
    """Abstract base class for scheduled Aily minds.

    Provides common infrastructure for:
    - APScheduler-based cron triggers
    - Circuit breaker integration
    - Session state tracking
    - Logging and observability
    """

    def __init__(
        self,
        llm_client: LLMClient,
        mind_name: str,
        schedule_hour: int,
        schedule_minute: int,
        circuit_breaker_threshold: int = 3,
        enabled: bool = True,
    ) -> None:
        self.llm_client = llm_client
        self.mind_name = mind_name
        self.schedule_hour = schedule_hour
        self.schedule_minute = schedule_minute
        self.enabled = enabled

        # Circuit breaker for failure handling
        self.circuit_breaker = CircuitBreakerMixin(
            failure_threshold=circuit_breaker_threshold,
        )

        # Scheduler infrastructure
        self.scheduler = AsyncIOScheduler()
        self._current_session: SessionResult | None = None
        self._session_history: list[SessionResult] = []

    def start(self) -> None:
        """Start the scheduler if enabled."""
        if not self.enabled:
            logger.info("[%s] Mind is disabled, not starting scheduler", self.mind_name)
            return

        self.scheduler.start()
        self.scheduler.add_job(
            self._run_session_wrapper,
            trigger=CronTrigger(hour=self.schedule_hour, minute=self.schedule_minute),
            id=f"{self.mind_name}_session",
            replace_existing=True,
            max_instances=1,  # Prevent overlapping sessions
        )
        logger.info(
            "[%s] Scheduler started (daily at %02d:%02d)",
            self.mind_name,
            self.schedule_hour,
            self.schedule_minute,
        )

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("[%s] Scheduler stopped", self.mind_name)

    async def _run_session_wrapper(self) -> None:
        """Wrapper for session execution with circuit breaker and error handling."""
        if not self.enabled:
            logger.info("[%s] Session skipped: mind is disabled", self.mind_name)
            return

        # Check circuit breaker
        if not await self.circuit_breaker.can_execute():
            logger.warning(
                "[%s] Session skipped: circuit breaker is open",
                self.mind_name,
            )
            return

        # Create session record
        session_id = f"{self.mind_name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        self._current_session = SessionResult(
            session_id=session_id,
            mind_name=self.mind_name,
            state=SessionState.RUNNING,
            started_at=datetime.now(timezone.utc),
        )

        try:
            logger.info("[%s] Starting session %s", self.mind_name, session_id)

            # Execute the actual session logic
            result = await self._run_session()

            # Record success
            await self.circuit_breaker.record_success()

            # Update session result
            self._current_session.state = SessionState.COMPLETED
            self._current_session.completed_at = datetime.now(timezone.utc)
            self._current_session.proposals_generated = result.get("proposals_generated", 0)
            self._current_session.proposals_delivered = result.get("proposals_delivered", 0)
            self._current_session.metadata = result.get("metadata", {})

            logger.info(
                "[%s] Session completed: %d proposals generated, %d delivered",
                self.mind_name,
                self._current_session.proposals_generated,
                self._current_session.proposals_delivered,
            )

        except Exception as exc:
            logger.exception("[%s] Session failed: %s", self.mind_name, exc)

            # Record failure and check if circuit should trip
            tripped = await self.circuit_breaker.record_failure()

            # Update session result
            self._current_session.state = SessionState.FAILED if not tripped else SessionState.CIRCUIT_OPEN
            self._current_session.completed_at = datetime.now(timezone.utc)
            self._current_session.error_message = str(exc)

            if tripped:
                logger.error(
                    "[%s] Circuit breaker tripped. Mind disabled until manual reset.",
                    self.mind_name,
                )

        finally:
            # Archive session
            self._session_history.append(self._current_session)
            if len(self._session_history) > 100:  # Keep last 100 sessions
                self._session_history = self._session_history[-100:]

    @abstractmethod
    async def _run_session(self) -> dict[str, Any]:
        """Execute the actual mind session.

        Must be implemented by subclasses. Should return a dict with:
        - proposals_generated: int
        - proposals_delivered: int
        - metadata: dict (optional)

        Raises:
            Exception on failure (will be caught and logged by wrapper).
        """
        pass

    def get_status(self) -> dict[str, Any]:
        """Get current status of this mind."""
        return {
            "mind_name": self.mind_name,
            "enabled": self.enabled,
            "schedule": f"{self.schedule_hour:02d}:{self.schedule_minute:02d}",
            "circuit_breaker": self.circuit_breaker.get_circuit_status(),
            "current_session": {
                "id": self._current_session.session_id if self._current_session else None,
                "state": self._current_session.state.name if self._current_session else None,
            },
            "total_sessions": len(self._session_history),
            "recent_failures": sum(
                1 for s in self._session_history[-10:]
                if s.state == SessionState.FAILED
            ),
        }

    async def enable(self) -> None:
        """Enable this mind (via Feishu command)."""
        self.enabled = True
        await self.circuit_breaker.reset()
        self.start()
        logger.info("[%s] Mind enabled", self.mind_name)

    async def disable(self) -> None:
        """Disable this mind (via Feishu command)."""
        self.enabled = False
        self.stop()
        logger.info("[%s] Mind disabled", self.mind_name)
