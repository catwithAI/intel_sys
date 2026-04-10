from __future__ import annotations

import json
import logging
import time

from app.config import settings
from app.corroboration.service import CorroborationService
from app.engine.context import RuleContext
from app.engine.registry import rule_registry
from app.models import AIEnrichment, Alert, Event, Severity, SourceType
from app.sources.github import GitHubSource

logger = logging.getLogger(__name__)


def _read_pushed_ts(raw: str | None, snapshot_raw: str | None) -> int | None:
    """Parse pushed key value to a timestamp.

    New format: ``{"ts": 1707500000}``
    Old format: ``"1"`` — fall back to star_snapshot ts, then 7 days ago.
    """
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "ts" in data:
            return int(data["ts"])
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    # Old format "1" — try star_snapshot ts
    if snapshot_raw:
        try:
            snap = json.loads(snapshot_raw)
            if isinstance(snap, dict) and "ts" in snap:
                return int(snap["ts"])
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    # Ultimate fallback: 7 days ago
    return int(time.time()) - 7 * 86400


@rule_registry.register(
    source="github",
    schedule="cron:0 17 * * *",
    trigger="batch",
)
async def discover_trending_repos(ctx: RuleContext) -> bool:
    """Discover trending GitHub repos via star-delta anomalies + gtrending."""
    source = GitHubSource()

    try:
        # ------------------------------------------------------------------
        # 1. Build observation pool + fetch gtrending
        # ------------------------------------------------------------------
        pool_events = await source.fetch()
        gtrending_events = await source.fetch_gtrending(settings.github_gtrending_languages)

        logger.info(
            "Pool: %d repos, gtrending: %d repos", len(pool_events), len(gtrending_events)
        )

        # ------------------------------------------------------------------
        # 2. Star-delta anomaly detection on pool
        # ------------------------------------------------------------------
        anomalies: dict[str, Event] = {}  # keyed by full_name for dedup
        now_ts = int(time.time())

        for event in pool_events:
            fn = event.source_id
            stars = event.data["stars"]
            snapshot_key = f"gh:repo:{fn}:star_snapshot"
            old_raw = await ctx.db.get(snapshot_key)

            # Write new snapshot
            await ctx.db.set(
                snapshot_key,
                json.dumps({"stars": stars, "ts": now_ts}),
                ex=30 * 86400,
            )

            if old_raw:
                old_data = json.loads(old_raw)
                delta = stars - old_data["stars"]
                if delta >= settings.github_star_delta_threshold:
                    event.data["star_delta"] = delta
                    event.metadata["strategy"] = "star_delta"
                    anomalies[fn] = event
                    logger.info("Star delta anomaly: %s  +%d stars", fn, delta)

        logger.info(
            "Star-delta anomalies: %d (first run has 0 — no prior snapshots)",
            len(anomalies),
        )

        # ------------------------------------------------------------------
        # 3. Merge gtrending repos (no per-repo API calls for topics)
        # ------------------------------------------------------------------
        for event in gtrending_events:
            fn = event.source_id
            if fn in anomalies:
                # Already detected via star-delta — boost it
                anomalies[fn].data.setdefault("current_period_stars", event.data.get("current_period_stars", 0))
                anomalies[fn].metadata["strategy"] = "star_delta+gtrending"
                continue

            event.metadata["strategy"] = "gtrending"
            anomalies[fn] = event

        logger.info("After gtrending merge: %d total anomalies", len(anomalies))

        if not anomalies:
            logger.info("No anomalies detected — done")
            return False

        # ------------------------------------------------------------------
        # 4. Sort + split into new vs returning candidates
        # ------------------------------------------------------------------
        candidates = list(anomalies.values())
        candidates.sort(
            key=lambda e: (e.data.get("star_delta", 0), e.data.get("current_period_stars", 0)),
            reverse=True,
        )

        new_candidates: list[Event] = []
        returning_candidates: list[tuple[Event, int]] = []  # (event, last_pushed_ts)

        for event in candidates:
            fn = event.source_id
            pushed_key = f"gh:repo:{fn}:pushed"
            pushed_raw = await ctx.db.get(pushed_key)

            if pushed_raw is None:
                new_candidates.append(event)
            else:
                snapshot_raw = await ctx.db.get(f"gh:repo:{fn}:star_snapshot")
                last_ts = _read_pushed_ts(pushed_raw, snapshot_raw)
                if last_ts is not None:
                    returning_candidates.append((event, last_ts))

        logger.info(
            "Split: %d new, %d returning (%d total)",
            len(new_candidates),
            len(returning_candidates),
            len(candidates),
        )

        # ------------------------------------------------------------------
        # 5. New project flow (existing logic, unchanged)
        # ------------------------------------------------------------------
        all_alerts: list[Alert] = []
        alerts_created = 0
        skipped_count = 0

        top = new_candidates[:20]
        if top:
            logger.info(
                "Top %d new candidates: %s",
                len(top),
                ", ".join(
                    f"{e.source_id}(Δ{e.data.get('star_delta', '?')})"
                    for e in top[:5]
                ),
            )

        for event in top:
            full_name = event.source_id

            # Fetch README (reuse same source instance)
            readme = await source.fetch_readme(full_name)

            # Build template context
            tmpl_ctx = {
                "name": event.data.get("name", ""),
                "full_name": full_name,
                "description": event.data.get("description", ""),
                "language": event.data.get("language", ""),
                "stars": event.data.get("stars", 0),
                "forks": event.data.get("forks", 0),
                "created_at": event.data.get("created_at", ""),
                "topics": event.data.get("topics", []),
                "readme": readme,
                "discovery_strategy": event.metadata.get("strategy", "unknown"),
                "star_delta": event.data.get("star_delta"),
                "current_period_stars": event.data.get("current_period_stars"),
            }

            try:
                ai_result = await ctx.ai.analyze(
                    "github/project_evaluation.jinja2", tmpl_ctx
                )
            except Exception:
                logger.exception("AI analysis failed for %s", full_name)
                ai_result = {}

            recommendation = ai_result.get("recommendation", "skip")
            logger.info(
                "AI result for %s: recommendation=%s, score=%s",
                full_name,
                recommendation,
                ai_result.get("innovation_score", "?"),
            )
            if recommendation == "skip":
                skipped_count += 1
                continue

            severity = Severity.HIGH if recommendation == "worth_watching" else Severity.MEDIUM

            # Build title with delta info
            delta_str = ""
            if event.data.get("star_delta"):
                delta_str = f" +{event.data['star_delta']}Δ"
            elif event.data.get("current_period_stars"):
                delta_str = f" +{event.data['current_period_stars']}☆today"

            try:
                confidence = float(ai_result.get("innovation_score", 0))
            except (ValueError, TypeError):
                confidence = 0.0

            enrichment = AIEnrichment(
                summary=ai_result.get("summary", ""),
                analysis=json.dumps(ai_result, ensure_ascii=False),
                confidence=confidence,
            )

            alert = Alert(
                source=SourceType.GITHUB,
                rule_name="discover_trending_repos",
                severity=severity,
                title=f"[{event.data.get('language', '?')}] {full_name} ({event.data.get('stars', 0)}★{delta_str})",
                event=event,
                enrichment=enrichment,
            )

            # Corroboration: search HN/Twitter for supporting evidence
            corroboration_svc = CorroborationService()
            try:
                corr = await corroboration_svc.search(alert)
                if corr:
                    alert.corroboration = corr.to_dict()
                    new_conf = min(max(alert.enrichment.confidence + corr.confidence_boost, 0.0), 1.0)
                    alert.enrichment.confidence = new_conf
                    if corr.confidence_boost >= 0.15 and alert.severity == Severity.MEDIUM:
                        alert.severity = Severity.HIGH
            finally:
                await corroboration_svc.close()

            await ctx.db.lpush("alerts:github", alert.model_dump_json())
            await ctx.db.ltrim("alerts:github", 0, settings.alert_max_per_source - 1)
            await ctx.db.set(
                f"gh:repo:{full_name}:pushed",
                json.dumps({"ts": now_ts, "stars": event.data.get("stars", 0)}),
                ex=30 * 86400,
            )
            all_alerts.append(alert)

            alerts_created += 1
            logger.info("Alert created for %s: %s", full_name, recommendation)

        # ------------------------------------------------------------------
        # 6. Update flow for returning candidates
        # ------------------------------------------------------------------
        update_alerts = 0
        top_returning = returning_candidates[:10]

        for event, last_ts in top_returning:
            full_name = event.source_id

            merged_prs = await source.fetch_merged_prs(full_name, last_ts)
            if not merged_prs:
                logger.info("No new merged PRs for %s since %d", full_name, last_ts)
                continue

            days_since = max(1, (now_ts - last_ts) // 86400)

            # Inject last_pushed_ts for delivery card
            event.data["last_pushed_ts"] = last_ts

            # Calculate star delta since last push
            pushed_raw = await ctx.db.get(f"gh:repo:{full_name}:pushed")
            if pushed_raw:
                try:
                    pushed_data = json.loads(pushed_raw)
                    if isinstance(pushed_data, dict) and "stars" in pushed_data:
                        last_stars = int(pushed_data["stars"])
                        current_stars = event.data.get("stars", 0)
                        event.data["star_delta_since_push"] = current_stars - last_stars
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            # Fetch README for AI project summary
            readme = await source.fetch_readme(full_name)

            tmpl_ctx = {
                "full_name": full_name,
                "description": event.data.get("description", ""),
                "language": event.data.get("language", ""),
                "stars": event.data.get("stars", 0),
                "pr_count": len(merged_prs),
                "pull_requests": merged_prs,
                "days_since_last_push": days_since,
                "readme": readme,
            }

            try:
                ai_result = await ctx.ai.analyze(
                    "github/project_update.jinja2", tmpl_ctx
                )
            except Exception:
                logger.exception("AI update analysis failed for %s", full_name)
                continue

            try:
                confidence = float(ai_result.get("activity_score", 0))
            except (ValueError, TypeError):
                confidence = 0.0

            enrichment = AIEnrichment(
                summary=ai_result.get("summary", ""),
                analysis=json.dumps(ai_result, ensure_ascii=False),
                confidence=confidence,
            )

            stars = event.data.get("stars", 0)
            alert = Alert(
                source=SourceType.GITHUB,
                rule_name="discover_trending_repos",
                severity=Severity.LOW,
                title=f"[更新] {full_name} ({stars}★ | {len(merged_prs)} PRs)",
                event=event,
                enrichment=enrichment,
            )

            # Corroboration: search HN/Twitter for supporting evidence
            corroboration_svc = CorroborationService()
            try:
                corr = await corroboration_svc.search(alert)
                if corr:
                    alert.corroboration = corr.to_dict()
                    new_conf = min(max(alert.enrichment.confidence + corr.confidence_boost, 0.0), 1.0)
                    alert.enrichment.confidence = new_conf
                    if corr.confidence_boost >= 0.15 and alert.severity == Severity.MEDIUM:
                        alert.severity = Severity.HIGH
            finally:
                await corroboration_svc.close()

            await ctx.db.lpush("alerts:github", alert.model_dump_json())
            await ctx.db.ltrim("alerts:github", 0, settings.alert_max_per_source - 1)
            # Refresh pushed key with current timestamp + stars
            await ctx.db.set(
                f"gh:repo:{full_name}:pushed",
                json.dumps({"ts": now_ts, "stars": event.data.get("stars", 0)}),
                ex=30 * 86400,
            )
            all_alerts.append(alert)

            update_alerts += 1
            logger.info("Update alert created for %s (%d PRs)", full_name, len(merged_prs))

        # ------------------------------------------------------------------
        # 7. Batch delivery
        # ------------------------------------------------------------------
        if all_alerts:
            await ctx.delivery.send_batch(all_alerts)

        # ------------------------------------------------------------------
        # 8. Summary
        # ------------------------------------------------------------------
        logger.info(
            "GitHub rule completed: %d new alerts, %d update alerts, %d skipped, %d candidates total",
            alerts_created,
            update_alerts,
            skipped_count,
            len(candidates),
        )
        return (alerts_created + update_alerts) > 0

    finally:
        await source.stop()
