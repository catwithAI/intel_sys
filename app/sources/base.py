from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import Event


class BaseSource(ABC):
    """Abstract base for all data sources."""

    @abstractmethod
    async def fetch(self) -> list[Event]:
        """Fetch and return standardized events."""
        ...

    async def start(self) -> None:
        """Optional: start long-running connections (e.g. WebSocket)."""

    async def stop(self) -> None:
        """Optional: clean up connections."""
