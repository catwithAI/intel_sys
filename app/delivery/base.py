from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import Alert


class BaseDelivery(ABC):
    """Abstract base for alert delivery channels."""

    @abstractmethod
    async def send(self, alert: Alert) -> bool:
        """Send an alert. Returns True on success."""
        ...

    async def close(self) -> None:
        """Release resources. Override if needed."""
