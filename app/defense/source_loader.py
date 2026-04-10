from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.defense.models import SourceSpec

logger = logging.getLogger(__name__)


class SourceLoader:
    """Loads defense source specs from YAML files."""

    @staticmethod
    def load_defense_sources(directory: str) -> list[SourceSpec]:
        """Load all defense_*.yaml files from directory, returning enabled specs."""
        base = Path(directory)
        specs: list[SourceSpec] = []
        seen_ids: set[str] = set()

        for path in sorted(base.glob("defense_*.yaml")):
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Failed to parse %s, skipping", path)
                continue

            if not isinstance(raw, list):
                logger.warning("Expected list in %s, skipping", path)
                continue

            for entry in raw:
                if not isinstance(entry, dict):
                    logger.warning("Non-dict entry in %s, skipping", path)
                    continue

                try:
                    spec = SourceSpec.model_validate(entry)
                except Exception:
                    logger.warning("Invalid spec in %s: %s, skipping", path, entry.get("id", "?"))
                    continue

                if not spec.enabled:
                    continue

                if not spec.access.allow_fetch:
                    continue

                if spec.id in seen_ids:
                    logger.warning("Duplicate id '%s' in %s, skipping", spec.id, path)
                    continue

                seen_ids.add(spec.id)
                specs.append(spec)

        return specs
