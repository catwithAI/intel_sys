from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app.defense.models import NormalizedEvent

logger = logging.getLogger(__name__)

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS normalized_events (
    id                 BIGSERIAL PRIMARY KEY,
    run_id             TEXT NOT NULL,
    source_id          TEXT NOT NULL,
    site_id            TEXT NOT NULL,
    site_name          TEXT,
    family             TEXT NOT NULL,
    country            TEXT,
    language           TEXT,
    title              TEXT NOT NULL,
    body               TEXT,
    url                TEXT,
    canonical_url      TEXT,
    published_at       TIMESTAMPTZ,
    source_weight      REAL,
    extraction_quality REAL,
    pre_score          REAL,
    url_hash           TEXT,
    content_hash       TEXT,
    dedup_keys         JSONB,
    raw_metadata       JSONB,
    created_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ne_site_id ON normalized_events (site_id);
CREATE INDEX IF NOT EXISTS idx_ne_run_id ON normalized_events (run_id);
CREATE INDEX IF NOT EXISTS idx_ne_published_at ON normalized_events (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_ne_created_at ON normalized_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ne_url_hash ON normalized_events (url_hash);

CREATE TABLE IF NOT EXISTS run_history (
    id          TEXT PRIMARY KEY,
    rule_name   TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL,
    stats       JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS source_health (
    site_id              TEXT PRIMARY KEY,
    status               TEXT NOT NULL DEFAULT 'ok',
    last_success_at      TIMESTAMPTZ,
    last_failure_at      TIMESTAMPTZ,
    last_error           TEXT,
    consecutive_failures INT DEFAULT 0,
    total_fetches        INT DEFAULT 0,
    total_failures       INT DEFAULT 0,
    cooldown_until       TIMESTAMPTZ,
    disabled_reason      TEXT,
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);
"""


class DefenseStorage:
    """PostgreSQL persistence layer for defense pipeline."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def init_tables(self) -> None:
        """Create tables if they don't exist."""
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_TABLES_SQL)

    async def insert_normalized_events(
        self, events: list[NormalizedEvent], run_id: str
    ) -> int:
        """Append-only insert of normalized events. Returns count inserted."""
        if not events:
            return 0

        async with self._pool.acquire() as conn:
            rows = [
                (
                    run_id,
                    e.source_id,
                    e.site_id,
                    e.site_name,
                    e.family,
                    e.country,
                    e.language,
                    e.title,
                    e.body,
                    e.url,
                    e.canonical_url,
                    e.published_at,
                    e.source_weight,
                    e.extraction_quality,
                    e.pre_score,
                    e.dedup_keys.get("url_hash", ""),
                    e.dedup_keys.get("content_hash", ""),
                    json.dumps(e.dedup_keys),
                    json.dumps(e.raw_metadata),
                )
                for e in events
            ]
            await conn.executemany(
                """INSERT INTO normalized_events
                   (run_id, source_id, site_id, site_name, family, country, language,
                    title, body, url, canonical_url, published_at, source_weight,
                    extraction_quality, pre_score, url_hash, content_hash, dedup_keys, raw_metadata)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)""",
                rows,
            )
            return len(rows)

    async def insert_run(
        self,
        run_id: str,
        rule_name: str,
        started_at: datetime,
        finished_at: datetime,
        status: str,
        stats: dict[str, Any],
    ) -> None:
        """Insert a run history record (append-only)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO run_history (id, rule_name, started_at, finished_at, status, stats)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                run_id, rule_name, started_at, finished_at, status, json.dumps(stats),
            )

    async def upsert_source_health(self, site_id: str, payload: dict[str, Any]) -> None:
        """Atomic upsert of source health record using INSERT ... ON CONFLICT."""
        async with self._pool.acquire() as conn:
            cols = ["site_id", *payload.keys()]
            placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
            col_names = ", ".join(cols)
            updates = ", ".join(f"{k} = EXCLUDED.{k}" for k in payload.keys())
            await conn.execute(
                f"""INSERT INTO source_health ({col_names}) VALUES ({placeholders})
                    ON CONFLICT (site_id) DO UPDATE SET {updates}, updated_at = NOW()""",
                site_id, *payload.values(),
            )

    async def get_source_health(self) -> list[dict[str, Any]]:
        """Get all source health records."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM source_health")
            return [dict(row) for row in rows]
