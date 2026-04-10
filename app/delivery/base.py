from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import Alert


class BaseDelivery(ABC):
    """Abstract base for alert delivery channels."""

    @abstractmethod
    async def send(self, alert: Alert) -> bool:
        """Send an alert. Returns True on success."""
        ...

    async def send_batch(self, alerts: list[Alert]) -> bool:
        """Send multiple alerts as a single message. Default: send individually."""
        results = [await self.send(a) for a in alerts]
        return any(results)

    async def close(self) -> None:
        """Release resources. Override if needed."""
