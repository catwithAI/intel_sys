from __future__ import annotations

import hashlib
import hmac
import base64
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.delivery.base import BaseDelivery
from app.models import Alert, Severity, SourceType

logger = logging.getLogger(__name__)

_SEVERITY_COLORS: dict[Severity, str] = {
    Severity.CRITICAL: "red",
    Severity.HIGH: "orange",
    Severity.MEDIUM: "blue",
    Severity.LOW: "grey",
}


def _md(content: str) -> dict:
    """Shortcut for a lark_md div element."""
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _collapsible(title: str, elements: list[dict], expanded: bool = False) -> dict:
    """Build a Card JSON v2 collapsible_panel element."""
    return {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": {
            "title": {
                "tag": "plain_text",
                "content": title,
            },
        },
        "vertical_spacing": "8px",
        "elements": elements,
    }


class FeishuWebhookDelivery(BaseDelivery):
    """Send alerts to a Feishu group via custom bot webhook."""

    def __init__(self, webhook_url: str, secret: str = "") -> None:
        self._webhook_url = webhook_url
        self._secret = secret
        self._http = httpx.AsyncClient(timeout=10)

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------
    def _gen_sign(self, timestamp: int) -> str:
        string_to_sign = f"{timestamp}\n{self._secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    # ------------------------------------------------------------------
    # Card formatting (Card JSON v2)
    # ------------------------------------------------------------------
    def _format_alert(self, alert: Alert) -> dict[str, Any]:
        color = _SEVERITY_COLORS.get(alert.severity, "blue")

        if alert.source == SourceType.GITHUB:
            elements = self._format_github_card(alert)
        elif alert.source == SourceType.POLYMARKET:
            elements = self._format_polymarket_card(alert)
        elif alert.source == SourceType.HACKERNEWS:
            elements = self._format_hackernews_card(alert)
        elif alert.source == SourceType.CORRELATION:
            elements = self._format_correlation_card(alert)
        else:
            elements = self._format_generic_card(alert)

        # Append corroboration panel if available
        corr_panel = self._format_corroboration_panel(alert.corroboration)
        if corr_panel:
            elements.append(corr_panel)

        return {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {
                    "title": {"tag": "plain_text", "content": alert.title},
                    "template": color,
                },
                "body": {
                    "elements": elements,
                },
            },
        }

    # ------------------------------------------------------------------
    # GitHub cards
    # ------------------------------------------------------------------
    def _format_github_card(self, alert: Alert) -> list[dict]:
        if alert.title.startswith("[更新]"):
            return self._format_github_update_card(alert)

        event = alert.event
        lang = event.data.get("language", "?")
        stars = event.data.get("stars", 0)
        forks = event.data.get("forks", 0)
        strategy = event.metadata.get("strategy", "unknown")
        desc = event.data.get("description", "")
        summary = alert.enrichment.summary or ""
        confidence = alert.enrichment.confidence
        url = f"https://github.com/{event.source_id}"

        # -- Summary section (always visible) --
        summary_lines = []
        if desc:
            summary_lines.append(desc)
        if summary:
            summary_lines.append(f"\n**AI 评估**\n{summary}")
        summary_lines.append(f"\n[查看仓库]({url})")

        elements: list[dict] = [_md("\n".join(summary_lines))]

        # -- Details section (collapsed) --
        detail = (
            f"语言: {lang} | Star: {stars} | Fork: {forks}\n"
            f"策略: {strategy} | 置信度: {confidence:.2f}"
        )
        elements.append(_collapsible("详细信息", [_md(detail)]))

        return elements

    def _format_github_update_card(self, alert: Alert) -> list[dict]:
        event = alert.event
        lang = event.data.get("language", "?")
        stars = event.data.get("stars", 0)
        confidence = alert.enrichment.confidence
        url = f"https://github.com/{event.source_id}"

        # Parse AI analysis JSON
        ai_data: dict = {}
        if alert.enrichment.analysis:
            try:
                ai_data = json.loads(alert.enrichment.analysis)
            except (json.JSONDecodeError, TypeError):
                pass

        project_summary = ai_data.get("project_summary", "")
        summary = ai_data.get("summary", alert.enrichment.summary or "")
        new_features = ai_data.get("new_features", [])
        improvements = ai_data.get("improvements", [])
        notable_prs = ai_data.get("notable_prs", [])
        trend = ai_data.get("development_trend", "stable")
        recommendation = ai_data.get("recommendation", "")

        trend_map = {"accelerating": "加速发展", "stable": "稳定", "slowing": "放缓"}
        trend_label = trend_map.get(trend, trend)

        last_pushed_ts = event.data.get("last_pushed_ts")
        star_delta = event.data.get("star_delta_since_push")

        # -- Summary section (always visible) --
        summary_lines = []
        intro = project_summary or event.data.get("description", "")
        if intro:
            summary_lines.append(intro)
        if summary:
            subtitle_parts = []
            if last_pushed_ts:
                push_date = datetime.fromtimestamp(last_pushed_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                subtitle_parts.append(f"上次推送: {push_date}")
            if star_delta is not None:
                sign = "+" if star_delta >= 0 else ""
                subtitle_parts.append(f"期间 Star {sign}{star_delta}")
            if subtitle_parts:
                subtitle = "（" + "，".join(subtitle_parts) + "）"
                summary_lines.append(f"\n**动态摘要**{subtitle}\n{summary}")
            else:
                summary_lines.append(f"\n**动态摘要**\n{summary}")
        summary_lines.append(f"\n[查看仓库]({url})")

        elements: list[dict] = [_md("\n".join(summary_lines))]

        # -- Details section (collapsed) --
        detail_lines = []
        if new_features:
            detail_lines.append("**新增功能**")
            for feat in new_features:
                detail_lines.append(f"- {feat}")
        if improvements:
            detail_lines.append("\n**改进优化**")
            for imp in improvements:
                detail_lines.append(f"- {imp}")
        if notable_prs:
            detail_lines.append("\n**重要 PR**")
            for pr in notable_prs:
                num = pr.get("number", "?")
                sig = pr.get("significance", pr.get("title", ""))
                detail_lines.append(f"- #{num}: {sig}")
        if recommendation:
            detail_lines.append(f"\n**建议**: {recommendation}")
        detail_lines.append(
            f"\n语言: {lang} | Star: {stars} | 趋势: {trend_label} | 活跃度: {confidence:.2f}"
        )

        if detail_lines:
            elements.append(_collapsible("详细信息", [_md("\n".join(detail_lines))]))

        return elements

    # ------------------------------------------------------------------
    # Polymarket cards
    # ------------------------------------------------------------------
    def _format_polymarket_card(self, alert: Alert) -> list[dict]:
        event = alert.event
        signals = event.data.get("signals", [])
        anomaly_score = event.data.get("anomaly_score", 0)
        confidence = alert.enrichment.confidence
        end_date = event.data.get("end_date", "")
        event_slug = event.data.get("event_slug", "")

        # Parse AI analysis JSON
        ai_data: dict = {}
        if alert.enrichment.analysis:
            try:
                ai_data = json.loads(alert.enrichment.analysis)
            except (json.JSONDecodeError, TypeError):
                pass

        question = event.data.get("question", "")
        question_zh = ai_data.get("question_zh", "")
        summary = ai_data.get("summary", alert.enrichment.summary or "")
        geopolitical_impact = ai_data.get("geopolitical_impact", "")
        trading = ai_data.get("trading_suggestion", {})
        direction = trading.get("direction", "")
        reasoning = trading.get("reasoning", "")
        trade_outcome = trading.get("outcome", "")
        trade_price = trading.get("price", 0)

        direction_map = {
            "buy_yes": "建议买入 Yes",
            "buy_no": "建议买入 No",
            "hold": "建议观望",
            "avoid": "建议回避",
        }
        direction_zh = direction_map.get(direction, direction)

        breaking_score = event.data.get("breaking_score", 0)

        # -- Summary section (always visible) --
        summary_lines = []
        if breaking_score >= 2.0:
            summary_lines.append("🔴 **BREAKING**")
        if question_zh:
            summary_lines.append(f"**{question_zh}**")
        if question:
            summary_lines.append(question)
        if summary:
            summary_lines.append(f"\n{summary}")
        if event_slug:
            summary_lines.append(f"\n[查看市场](https://polymarket.com/event/{event_slug})")

        elements: list[dict] = [_md("\n".join(summary_lines))]

        # -- Signals section (collapsed) --
        if signals:
            # Separate wide (Tier 1) and deep (Tier 2) signals
            wide_sigs = [s for s in signals if s["type"].startswith("Wide:")]
            deep_sigs = [s for s in signals if s["type"].startswith("Deep:")]
            other_sigs = [s for s in signals if not s["type"].startswith(("Wide:", "Deep:"))]

            sig_lines = []
            if wide_sigs:
                sig_lines.append("**广域扫描信号**")
                for s in wide_sigs:
                    sig_lines.append(f"- **{s['type']}**: {s.get('description', '')}")
            if deep_sigs:
                sig_lines.append("**深度分析信号**")
                for s in deep_sigs:
                    sig_lines.append(f"- **{s['type']}**: {s.get('description', '')}")
            if other_sigs:
                for s in other_sigs:
                    sig_lines.append(f"- **{s['type']}**: {s.get('description', '')}")

            footer_parts = []
            if end_date:
                footer_parts.append(f"结束时间: {end_date[:10]}")
            footer_parts.append(f"置信度: {confidence:.2f}")
            if anomaly_score:
                footer_parts.append(f"异动评分: {anomaly_score}")
            if breaking_score:
                footer_parts.append(f"Breaking: {breaking_score:.2f}")
            sig_lines.append(f"\n{' | '.join(footer_parts)}")
            elements.append(_collapsible("异动信号", [_md("\n".join(sig_lines))]))

        # -- Trading suggestion (always visible) --
        if direction_zh:
            trading_lines = [f"**{direction_zh}**"]
            # Show specific outcome and price for buy directions
            if direction in ("buy_yes", "buy_no") and trade_outcome:
                price_display = f"${trade_price:.4f}" if trade_price else ""
                if price_display:
                    payout = 1.0 / trade_price if trade_price > 0 else 0
                    trading_lines.append(
                        f"买入 **{trade_outcome}** @ {price_display}"
                        f"（若胜出回报 {payout:.1f}x）"
                    )
                else:
                    trading_lines.append(f"买入 **{trade_outcome}**")
            if reasoning:
                trading_lines.append(reasoning)
            elements.append(_md("\n".join(trading_lines)))

        # -- Geopolitical impact (collapsed) --
        if geopolitical_impact:
            elements.append(_collapsible("地缘影响", [_md(geopolitical_impact)]))

        return elements

    # ------------------------------------------------------------------
    # Hacker News cards
    # ------------------------------------------------------------------
    def _format_hackernews_card(self, alert: Alert) -> list[dict]:
        event = alert.event
        data = event.data
        points = data.get("points", 0)
        num_comments = data.get("num_comments", 0)
        author = data.get("author", "")
        url = data.get("url", "")
        hn_url = data.get("hn_url", "")
        strategy = data.get("discovery_strategy", event.metadata.get("strategy", ""))

        # Parse AI analysis JSON
        ai_data: dict = {}
        if alert.enrichment.analysis:
            try:
                ai_data = json.loads(alert.enrichment.analysis)
            except (json.JSONDecodeError, TypeError):
                pass

        summary = ai_data.get("summary", alert.enrichment.summary or "")
        category = ai_data.get("category", "")
        topics = ai_data.get("topics", [])
        key_insights = ai_data.get("key_insights", [])
        impact_assessment = ai_data.get("impact_assessment", "")

        # -- Summary section (always visible) --
        summary_lines = []
        if summary:
            summary_lines.append(summary)
        links = []
        if url:
            links.append(f"[原文链接]({url})")
        if hn_url:
            links.append(f"[HN 讨论]({hn_url})")
        if links:
            summary_lines.append("\n" + " | ".join(links))

        elements: list[dict] = [_md("\n".join(summary_lines))]

        # -- Details section (collapsed) --
        detail_lines = [
            f"得分: {points} | 评论: {num_comments} | 作者: {author}",
        ]
        if category:
            category_map = {
                "ai": "AI", "infrastructure": "基础设施", "security": "安全",
                "programming": "编程", "startup": "创业", "open_source": "开源",
                "industry_news": "行业资讯", "science": "科学", "other": "其他",
            }
            detail_lines.append(f"分类: {category_map.get(category, category)}")
        if topics:
            detail_lines.append(f"标签: {', '.join(topics)}")
        if strategy:
            detail_lines.append(f"发现策略: {strategy}")
        elements.append(_collapsible("详细信息", [_md("\n".join(detail_lines))]))

        # -- Key insights (collapsed) --
        if key_insights:
            insight_lines = []
            for ins in key_insights:
                insight_lines.append(f"- {ins}")
            elements.append(_collapsible("社区洞察", [_md("\n".join(insight_lines))]))

        # -- Impact assessment (collapsed) --
        if impact_assessment:
            elements.append(_collapsible("影响评估", [_md(impact_assessment)]))

        return elements

    # ------------------------------------------------------------------
    # Correlation cards
    # ------------------------------------------------------------------
    def _format_correlation_card(self, alert: Alert) -> list[dict]:
        data = alert.event.data
        ai_data: dict = {}
        if alert.enrichment.analysis:
            try:
                ai_data = json.loads(alert.enrichment.analysis)
            except (json.JSONDecodeError, TypeError):
                pass

        title = data.get("title", "")
        reasoning = ai_data.get("reasoning", alert.enrichment.summary or "")
        direction = ai_data.get("investment_direction", "")
        chain = ai_data.get("chain", data.get("chain", []))
        confidence = ai_data.get("confidence", data.get("confidence", 0))
        timeframe = ai_data.get("timeframe", data.get("timeframe", ""))
        risks = ai_data.get("risks", data.get("risks", ""))
        category = ai_data.get("category", data.get("category", ""))

        # New dimensions (from data, populated by correlation_rules)
        cycle_phase = data.get("cycle_phase", "")
        crowdedness = data.get("crowdedness", 0)
        marginal_signals = data.get("marginal_signals", {})
        related_assets = data.get("related_assets", [])
        next_catalyst = data.get("next_catalyst", {})

        category_map = {
            "causal": "因果链",
            "supply_demand": "供需关系",
            "hedging": "对冲避险",
            "policy": "政策传导",
            "sentiment": "市场情绪",
        }
        cycle_map = {
            "genesis": "孕育期",
            "consensus": "共识期",
            "euphoria": "狂热期",
            "denial": "否认期",
            "capitulation": "投降期",
            "recovery": "复苏期",
        }

        # -- Header line: category | cycle | crowdedness (always visible) --
        header_parts = []
        if category:
            header_parts.append(f"**{category_map.get(category, category)}**")
        if cycle_phase:
            header_parts.append(f"周期: {cycle_map.get(cycle_phase, cycle_phase)}")
        if crowdedness:
            header_parts.append(f"拥挤度 {crowdedness}%")
        header_line = " | ".join(header_parts) if header_parts else ""

        # -- Summary section (always visible) --
        summary_lines = [f"**{title}**"]
        if header_line:
            summary_lines.append(header_line)
        if reasoning:
            summary_lines.append(f"\n{reasoning}")
        if direction:
            summary_lines.append(f"\n**投资方向**: {direction}")
        elements: list[dict] = [_md("\n".join(summary_lines))]

        # -- Marginal signals (always visible, if present) --
        if marginal_signals and (
            marginal_signals.get("positive") or marginal_signals.get("negative")
        ):
            sig_lines = ["**今日边际变化**"]
            positive = marginal_signals.get("positive", [])
            negative = marginal_signals.get("negative", [])
            if positive:
                sig_lines.append(f"▲ 正向  {'；'.join(positive)}")
            if negative:
                sig_lines.append(f"▼ 负向  {'；'.join(negative)}")
            elements.append(_md("\n".join(sig_lines)))

        # -- Related assets (always visible, if present) --
        if related_assets:
            direction_arrow = {"up": "↑", "down": "↓"}
            asset_parts = []
            for a in related_assets:
                symbol = a.get("symbol", "")
                arrow = direction_arrow.get(a.get("expected_direction", ""), "")
                rationale = a.get("rationale", "")
                part = f"**{symbol}** {arrow}"
                if rationale:
                    part += f" {rationale}"
                asset_parts.append(part)
            elements.append(_md("**相关标的**  " + " | ".join(asset_parts)))

        # -- Event chain (collapsed) --
        if chain:
            chain_lines = [f"{i + 1}. {e}" for i, e in enumerate(chain)]
            elements.append(
                _collapsible("关联事件链", [_md("\n".join(chain_lines))])
            )

        # -- Catalyst & timeframe (collapsed) --
        catalyst_lines = []
        if next_catalyst and next_catalyst.get("event"):
            date_str = next_catalyst.get("date", "")
            catalyst_lines.append(
                f"**下个催化剂**: {next_catalyst['event']}"
                + (f" ({date_str})" if date_str else "")
            )
        if timeframe:
            catalyst_lines.append(f"**时间框架**: {timeframe}")
        if catalyst_lines:
            elements.append(
                _collapsible("催化剂与时间窗口", [_md("\n".join(catalyst_lines))])
            )

        # -- Risk (collapsed) --
        risk_lines = []
        if risks:
            risk_lines.append(risks)
        risk_lines.append(f"置信度: {confidence:.2f}")
        elements.append(
            _collapsible("风险提示", [_md("\n".join(risk_lines))])
        )

        return elements

    def _format_correlation_digest_card(self, alerts: list[Alert]) -> dict[str, Any]:
        """Aggregate multiple correlation insights into a single digest card."""
        color = "orange"
        count = len(alerts)
        header_text = f"关联推理洞察 Digest ({count} 条)"

        sorted_alerts = sorted(
            alerts,
            key=lambda a: a.enrichment.confidence,
            reverse=True,
        )

        elements: list[dict] = []
        summary_lines = [
            f"本次推送共 {count} 条去重后的关联推理洞察。",
            "已按置信度排序，并在推送前对重复投资方向做了合并。",
        ]
        elements.append(_md("\n".join(summary_lines)))

        for alert in sorted_alerts:
            data = alert.event.data
            direction = data.get("investment_direction", "")
            merged_count = int(data.get("merged_count", 1) or 1)
            merged_titles = data.get("merged_titles", []) or []
            chain = data.get("chain", []) or []
            related_assets = data.get("related_assets", []) or []

            panel_lines = []
            if direction:
                panel_lines.append(f"**投资方向**: {direction}")
            if alert.enrichment.summary:
                panel_lines.append(alert.enrichment.summary)
            panel_lines.append(f"置信度: {alert.enrichment.confidence:.2f}")

            if merged_count > 1 and merged_titles:
                panel_lines.append("\n**合并主题**")
                for title in merged_titles[:5]:
                    panel_lines.append(f"- {title}")
                if len(merged_titles) > 5:
                    panel_lines.append(f"- 另有 {len(merged_titles) - 5} 条相似洞察")

            if chain:
                panel_lines.append("\n**事件链**")
                for idx, item in enumerate(chain[:5], 1):
                    panel_lines.append(f"{idx}. {item}")

            if related_assets:
                asset_parts = []
                for asset in related_assets[:5]:
                    if not isinstance(asset, dict):
                        continue
                    symbol = asset.get("symbol", "")
                    move = asset.get("expected_direction", "")
                    rationale = asset.get("rationale", "")
                    part = f"**{symbol}**"
                    if move:
                        part += f" {move}"
                    if rationale:
                        part += f" {rationale}"
                    asset_parts.append(part)
                if asset_parts:
                    panel_lines.append("\n**相关标的**")
                    panel_lines.append(" | ".join(asset_parts))

            collapse_title = alert.title
            if merged_count > 1:
                collapse_title += f"（合并 {merged_count} 条）"
            elements.append(_collapsible(collapse_title, [_md("\n".join(panel_lines))]))

        return {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {
                    "title": {"tag": "plain_text", "content": header_text},
                    "template": color,
                },
                "body": {
                    "elements": elements,
                },
            },
        }

    # ------------------------------------------------------------------
    # Corroboration panel
    # ------------------------------------------------------------------
    def _format_corroboration_panel(self, corr: dict) -> dict | None:
        """Build a collapsible panel for SM corroboration evidence."""
        if not corr or not corr.get("has_evidence"):
            return None

        lines: list[str] = []

        # Summary
        summary = corr.get("summary", "")
        if summary:
            lines.append(summary)

        # HN stories (top 3)
        hn_stories = corr.get("hn_stories", [])[:3]
        if hn_stories:
            lines.append("\n**Hacker News**")
            for s in hn_stories:
                title = s.get("title", "")
                hn_url = s.get("hn_url", "")
                points = s.get("points", 0)
                comments = s.get("num_comments", 0)
                if hn_url:
                    lines.append(f"- [{title}]({hn_url}) ({points}↑ {comments}💬)")
                else:
                    lines.append(f"- {title} ({points}↑ {comments}💬)")

        # Tweets (top 3)
        tweets = corr.get("tweets", [])[:3]
        if tweets:
            lines.append("\n**Twitter**")
            for t in tweets:
                author = t.get("author", "")
                text = t.get("text", "")[:100]
                likes = t.get("likes", 0)
                url = t.get("url", "")
                if url:
                    lines.append(f"- [@{author}]({url}): {text}… ({likes}❤)")
                else:
                    lines.append(f"- @{author}: {text}… ({likes}❤)")

        # Confidence boost
        boost = corr.get("confidence_boost", 0)
        if boost != 0:
            sign = "+" if boost > 0 else ""
            lines.append(f"\n置信度调整: {sign}{boost:.2f}")

        if not lines:
            return None

        return _collapsible("Social Media 佐证", [_md("\n".join(lines))])

    # ------------------------------------------------------------------
    # Generic fallback
    # ------------------------------------------------------------------
    def _format_generic_card(self, alert: Alert) -> list[dict]:
        return [_md(alert.enrichment.summary or alert.title)]

    # ------------------------------------------------------------------
    # Polymarket digest card (batch)
    # ------------------------------------------------------------------
    def _format_pm_digest_card(self, alerts: list[Alert]) -> dict[str, Any]:
        """Aggregate multiple Polymarket alerts into a single digest card."""
        header_text = f"Polymarket 异动汇总 ({len(alerts)} 条 · 过去 6 小时)"

        # Header color from highest severity
        severity_order = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        best_idx = 0
        for a in alerts:
            try:
                idx = severity_order.index(a.severity)
            except ValueError:
                idx = 0
            if idx > best_idx:
                best_idx = idx
        color = _SEVERITY_COLORS.get(severity_order[best_idx], "blue")

        elements: list[dict] = []

        for alert in alerts:
            # Build inner content reusing existing polymarket card logic
            inner_elements = self._format_polymarket_card(alert)

            # Append corroboration if available
            corr_panel = self._format_corroboration_panel(alert.corroboration)
            if corr_panel:
                inner_elements.append(corr_panel)

            # Collapse title: question_zh (severity)
            ai_data: dict = {}
            if alert.enrichment.analysis:
                try:
                    ai_data = json.loads(alert.enrichment.analysis)
                except (json.JSONDecodeError, TypeError):
                    pass

            question_zh = ai_data.get("question_zh", "")
            collapse_title = question_zh or alert.event.data.get("question", "")[:60]
            collapse_title = f"{collapse_title} ({alert.severity.value})"

            elements.append(_collapsible(collapse_title, inner_elements))

        return {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {
                    "title": {"tag": "plain_text", "content": header_text},
                    "template": color,
                },
                "body": {
                    "elements": elements,
                },
            },
        }

    # ------------------------------------------------------------------
    # HackerNews digest card (batch)
    # ------------------------------------------------------------------
    def _format_hn_digest_card(self, alerts: list[Alert]) -> dict[str, Any]:
        """Aggregate multiple HackerNews alerts into a single digest card."""
        header_text = f"HackerNews 热门话题 ({len(alerts)} 篇)"

        # Header color from highest severity
        severity_order = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        best_idx = 0
        for a in alerts:
            try:
                idx = severity_order.index(a.severity)
            except ValueError:
                idx = 0
            if idx > best_idx:
                best_idx = idx
        color = _SEVERITY_COLORS.get(severity_order[best_idx], "blue")

        elements: list[dict] = []

        for alert in alerts:
            event = alert.event
            data = event.data
            points = data.get("points", 0)
            url = data.get("url", "")
            hn_url = data.get("hn_url", "")

            # Parse AI analysis JSON
            ai_data: dict = {}
            if alert.enrichment.analysis:
                try:
                    ai_data = json.loads(alert.enrichment.analysis)
                except (json.JSONDecodeError, TypeError):
                    pass

            summary = ai_data.get("summary", alert.enrichment.summary or "")
            category = ai_data.get("category", "")
            _category_map = {
                "ai": "AI", "infrastructure": "基础设施", "security": "安全",
                "programming": "编程", "startup": "创业", "open_source": "开源",
                "industry_news": "行业资讯", "science": "科学", "other": "其他",
            }
            category_label = _category_map.get(category, category)

            # Title + summary (always visible)
            title_text = data.get("title", "")
            visible_lines = [f"**[{category_label}] {title_text}** ({points}↑)"]
            if summary:
                visible_lines.append(summary)
            links = []
            if url:
                links.append(f"[原文链接]({url})")
            if hn_url:
                links.append(f"[HN 讨论]({hn_url})")
            if links:
                visible_lines.append(" | ".join(links))

            elements.append(_md("\n".join(visible_lines)))

            # Details (collapsed) — reuse full card logic
            detail_elements = []
            num_comments = data.get("num_comments", 0)
            author = data.get("author", "")
            topics = ai_data.get("topics", [])
            key_insights = ai_data.get("key_insights", [])
            impact_assessment = ai_data.get("impact_assessment", "")

            detail_lines = [f"得分: {points} | 评论: {num_comments} | 作者: {author}"]
            if topics:
                detail_lines.append(f"标签: {', '.join(topics)}")
            if key_insights:
                detail_lines.append("\n**社区洞察**")
                for ins in key_insights:
                    detail_lines.append(f"- {ins}")
            if impact_assessment:
                detail_lines.append(f"\n**影响评估**\n{impact_assessment}")

            # Corroboration
            corr_panel = self._format_corroboration_panel(alert.corroboration)

            inner = [_md("\n".join(detail_lines))]
            if corr_panel:
                inner.append(corr_panel)

            elements.append(_collapsible("详细信息", inner))

        return {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {
                    "title": {"tag": "plain_text", "content": header_text},
                    "template": color,
                },
                "body": {
                    "elements": elements,
                },
            },
        }

    # ------------------------------------------------------------------
    # GitHub digest card (batch)
    # ------------------------------------------------------------------
    def _format_github_digest_card(self, alerts: list[Alert]) -> dict[str, Any]:
        """Aggregate multiple GitHub alerts into a single digest card."""
        new_alerts = [a for a in alerts if not a.title.startswith("[更新]")]
        update_alerts = [a for a in alerts if a.title.startswith("[更新]")]

        # Header
        parts = []
        if new_alerts:
            parts.append(f"{len(new_alerts)} 个新项目")
        if update_alerts:
            parts.append(f"{len(update_alerts)} 个更新")
        header_text = f"GitHub Trending 日报 ({' · '.join(parts)})"

        # Determine header color from highest severity
        severity_order = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        best_idx = 0
        for a in alerts:
            try:
                idx = severity_order.index(a.severity)
            except ValueError:
                idx = 0
            if idx > best_idx:
                best_idx = idx
        color = _SEVERITY_COLORS.get(severity_order[best_idx], "blue")

        elements: list[dict] = []

        # -- New projects: title+desc visible, details collapsed --
        for alert in new_alerts:
            event = alert.event
            lang = event.data.get("language", "?")
            stars = event.data.get("stars", 0)
            forks = event.data.get("forks", 0)
            strategy = event.metadata.get("strategy", "unknown")
            desc = event.data.get("description", "")
            summary = alert.enrichment.summary or ""
            confidence = alert.enrichment.confidence
            url = f"https://github.com/{event.source_id}"

            # Delta display
            delta_str = ""
            if event.data.get("star_delta"):
                delta_str = f" +{event.data['star_delta']}Δ"
            elif event.data.get("current_period_stars"):
                delta_str = f" +{event.data['current_period_stars']}☆today"

            # Title line (always visible)
            title_line = f"**[{lang}] [{event.source_id}]({url})** ({stars:,}★{delta_str})"
            visible_lines = [title_line]
            if desc:
                visible_lines.append(desc)

            elements.append(_md("\n".join(visible_lines)))

            # Collapsed detail panel
            detail_lines = []
            if summary:
                detail_lines.append(f"**AI 评估**: {summary}")
            detail_lines.append(
                f"语言: {lang} | Star: {stars:,} | Fork: {forks:,}\n"
                f"策略: {strategy} | 置信度: {confidence:.2f}"
            )
            detail_lines.append(f"[查看仓库]({url})")

            # Corroboration
            corr_panel = self._format_corroboration_panel(alert.corroboration)

            detail_elements = [_md("\n".join(detail_lines))]
            if corr_panel:
                detail_elements.append(corr_panel)

            elements.append(_collapsible("详细信息", detail_elements))

        # -- Separator between new and update sections --
        if new_alerts and update_alerts:
            elements.append({"tag": "hr"})

        # -- Update projects: entire project collapsed --
        for alert in update_alerts:
            event = alert.event
            stars = event.data.get("stars", 0)
            url = f"https://github.com/{event.source_id}"

            # Parse AI analysis JSON
            ai_data: dict = {}
            if alert.enrichment.analysis:
                try:
                    ai_data = json.loads(alert.enrichment.analysis)
                except (json.JSONDecodeError, TypeError):
                    pass

            # Build inner content (reuse update card logic)
            inner_elements = self._format_github_update_card(alert)

            # Corroboration
            corr_panel = self._format_corroboration_panel(alert.corroboration)
            if corr_panel:
                inner_elements.append(corr_panel)

            # PR count from title or data
            pr_count = len(event.data.get("merged_prs", []))
            if not pr_count:
                # Try to extract from title like "[更新] user/repo (5000★ | 3 PRs)"
                m = re.search(r"(\d+)\s*PRs?\)?$", alert.title)
                if m:
                    pr_count = int(m.group(1))

            collapse_title = f"[更新] {event.source_id} ({stars:,}★"
            if pr_count:
                collapse_title += f" | {pr_count} PRs"
            collapse_title += ")"

            elements.append(_collapsible(collapse_title, inner_elements))

        return {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "header": {
                    "title": {"tag": "plain_text", "content": header_text},
                    "template": color,
                },
                "body": {
                    "elements": elements,
                },
            },
        }

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------
    async def _send_digest(self, payload: dict[str, Any], label: str = "digest") -> bool:
        """Send a pre-formatted digest card payload."""
        if self._secret:
            ts = int(time.time())
            payload["timestamp"] = str(ts)
            payload["sign"] = self._gen_sign(ts)

        try:
            resp = await self._http.post(self._webhook_url, json=payload)
            data = resp.json()
            if data.get("code") == 0:
                logger.info("Feishu %s sent", label)
                return True
            logger.warning("Feishu %s error: %s", label, data)
            return False
        except Exception:
            logger.exception("Failed to send Feishu %s", label)
            return False

    async def send_batch(self, alerts: list[Alert]) -> bool:
        """Send multiple alerts as digest cards grouped by source.

        GitHub / Polymarket / HackerNews / Correlation alerts are each aggregated into
        a single digest card. Other source alerts fall back to individual sends.
        """
        if not alerts:
            return False

        github_alerts = [a for a in alerts if a.source == SourceType.GITHUB]
        pm_alerts = [a for a in alerts if a.source == SourceType.POLYMARKET]
        hn_alerts = [a for a in alerts if a.source == SourceType.HACKERNEWS]
        correlation_alerts = [a for a in alerts if a.source == SourceType.CORRELATION]
        other_alerts = [
            a for a in alerts
            if a.source not in (
                SourceType.GITHUB,
                SourceType.POLYMARKET,
                SourceType.HACKERNEWS,
                SourceType.CORRELATION,
            )
        ]

        results: list[bool] = []

        if github_alerts:
            payload = self._format_github_digest_card(github_alerts)
            results.append(await self._send_digest(payload, f"GitHub digest ({len(github_alerts)} alerts)"))

        if pm_alerts:
            payload = self._format_pm_digest_card(pm_alerts)
            results.append(await self._send_digest(payload, f"Polymarket digest ({len(pm_alerts)} alerts)"))

        if hn_alerts:
            payload = self._format_hn_digest_card(hn_alerts)
            results.append(await self._send_digest(payload, f"HN digest ({len(hn_alerts)} alerts)"))

        if correlation_alerts:
            payload = self._format_correlation_digest_card(correlation_alerts)
            results.append(
                await self._send_digest(
                    payload,
                    f"Correlation digest ({len(correlation_alerts)} alerts)",
                )
            )

        for a in other_alerts:
            results.append(await self.send(a))

        return any(results)

    async def send(self, alert: Alert) -> bool:
        payload = self._format_alert(alert)

        if self._secret:
            ts = int(time.time())
            payload["timestamp"] = str(ts)
            payload["sign"] = self._gen_sign(ts)

        try:
            resp = await self._http.post(self._webhook_url, json=payload)
            data = resp.json()
            if data.get("code") == 0:
                logger.info("Feishu alert sent: %s", alert.title[:60])
                return True
            # If v2 schema fails, try fallback to v1
            if "schema" in str(data):
                logger.warning("Card v2 rejected, trying v1 fallback: %s", data)
                return await self._send_v1_fallback(alert)
            logger.warning("Feishu API error: %s", data)
            return False
        except Exception:
            logger.exception("Failed to send Feishu alert: %s", alert.title[:60])
            return False

    async def _send_v1_fallback(self, alert: Alert) -> bool:
        """Fall back to Card JSON v1 if v2 is not supported."""
        color = _SEVERITY_COLORS.get(alert.severity, "blue")
        v2_payload = self._format_alert(alert)
        # Convert v2 body.elements back to v1 elements
        body_elements = v2_payload["card"].get("body", {}).get("elements", [])
        # Flatten collapsible_panels into plain divs for v1
        flat: list[dict] = []
        for el in body_elements:
            if el.get("tag") == "collapsible_panel":
                title = el.get("header", {}).get("title", {}).get("content", "")
                if title:
                    flat.append(_md(f"**{title}**"))
                flat.extend(el.get("elements", []))
            else:
                flat.append(el)

        payload: dict[str, Any] = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": alert.title},
                    "template": color,
                },
                "elements": flat,
            },
        }
        if self._secret:
            ts = int(time.time())
            payload["timestamp"] = str(ts)
            payload["sign"] = self._gen_sign(ts)

        try:
            resp = await self._http.post(self._webhook_url, json=payload)
            data = resp.json()
            if data.get("code") == 0:
                logger.info("Feishu alert sent (v1 fallback): %s", alert.title[:60])
                return True
            logger.warning("Feishu v1 fallback also failed: %s", data)
            return False
        except Exception:
            logger.exception("v1 fallback failed: %s", alert.title[:60])
            return False

    async def close(self) -> None:
        await self._http.aclose()


class NoopDelivery(BaseDelivery):
    """No-op delivery used when no webhook is configured."""

    async def send(self, alert: Alert) -> bool:
        return True

    async def close(self) -> None:
        pass
