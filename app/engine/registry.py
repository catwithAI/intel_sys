from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


@dataclass
class RuleMeta:
    """Metadata attached to a registered rule function."""

    name: str
    source: str
    schedule: str  # e.g. "interval:30s", "cron:0 9 * * *"
    trigger: str  # "threshold" or "batch"
    fn: Callable[..., Coroutine[Any, Any, bool]]


class RuleRegistry:
    """Singleton registry that collects rules via decorators."""

    _instance: RuleRegistry | None = None

    def __new__(cls) -> RuleRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._rules = {}
        return cls._instance

    _rules: dict[str, RuleMeta]

    def register(
        self,
        source: str,
        schedule: str,
        trigger: str = "threshold",
    ) -> Callable:
        """Decorator to register a rule function."""

        def decorator(fn: Callable[..., Coroutine[Any, Any, bool]]) -> Callable:
            name = fn.__name__
            meta = RuleMeta(
                name=name,
                source=source,
                schedule=schedule,
                trigger=trigger,
                fn=fn,
            )
            self._rules[name] = meta
            logger.info("Registered rule: %s (source=%s, schedule=%s)", name, source, schedule)
            return fn

        return decorator

    @property
    def rules(self) -> dict[str, RuleMeta]:
        return dict(self._rules)

    def get_rules_by_source(self, source: str) -> list[RuleMeta]:
        return [r for r in self._rules.values() if r.source == source]

    def clear(self) -> None:
        self._rules.clear()

    def load_rules_from_package(self, package_path: str = "app.rules") -> None:
        """Import all modules under the rules package to trigger @register decorators."""
        try:
            pkg = importlib.import_module(package_path)
        except ImportError:
            logger.error("Cannot import rules package: %s", package_path)
            return

        for importer, modname, ispkg in pkgutil.iter_modules(pkg.__path__):
            full_name = f"{package_path}.{modname}"
            try:
                importlib.import_module(full_name)
                logger.info("Loaded rule module: %s", full_name)
            except Exception:
                logger.exception("Failed to load rule module: %s", full_name)

    def reload_rules(self, package_path: str = "app.rules") -> None:
        """Clear and re-import all rule modules."""
        self.clear()
        # Force re-import by removing cached modules
        import sys

        pkg = importlib.import_module(package_path)
        to_remove = [
            key for key in sys.modules if key.startswith(package_path + ".")
        ]
        for key in to_remove:
            del sys.modules[key]
        self.load_rules_from_package(package_path)


# Global singleton
rule_registry = RuleRegistry()
