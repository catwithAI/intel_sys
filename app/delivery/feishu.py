from __future__ import annotations

import hashlib
import hmac
import base64
import json
import logging
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
        else:
            elements = self._format_generic_card(alert)

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

        direction_map = {
            "buy_yes": "建议买入 Yes",
            "buy_no": "建议买入 No",
            "hold": "建议观望",
            "avoid": "建议回避",
        }
        direction_zh = direction_map.get(direction, direction)

        # -- Summary section (always visible) --
        summary_lines = []
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
            sig_lines = []
            for s in signals:
                sig_lines.append(f"- **{s['type']}**: {s.get('description', '')}")
            footer_parts = []
            if end_date:
                footer_parts.append(f"结束时间: {end_date[:10]}")
            footer_parts.append(f"置信度: {confidence:.2f}")
            if anomaly_score:
                footer_parts.append(f"异动评分: {anomaly_score}")
            sig_lines.append(f"\n{' | '.join(footer_parts)}")
            elements.append(_collapsible("异动信号", [_md("\n".join(sig_lines))]))

        # -- Trading suggestion (collapsed) --
        if direction_zh:
            trading_lines = [f"**{direction_zh}**"]
            if reasoning:
                trading_lines.append(reasoning)
            elements.append(_collapsible("交易建议", [_md("\n".join(trading_lines))]))

        # -- Geopolitical impact (collapsed) --
        if geopolitical_impact:
            elements.append(_collapsible("地缘影响", [_md(geopolitical_impact)]))

        return elements

    # ------------------------------------------------------------------
    # Generic fallback
    # ------------------------------------------------------------------
    def _format_generic_card(self, alert: Alert) -> list[dict]:
        return [_md(alert.enrichment.summary or alert.title)]

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------
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
