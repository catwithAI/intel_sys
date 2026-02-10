from __future__ import annotations

import re

from app.models import Alert, SourceType

_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could", "of", "in", "to",
    "for", "with", "on", "at", "by", "from", "as", "into", "through",
    "during", "before", "after", "above", "below", "between", "out",
    "off", "over", "under", "again", "further", "then", "once", "and",
    "but", "or", "nor", "not", "so", "yet", "both", "either", "neither",
    "each", "every", "all", "any", "few", "more", "most", "other", "some",
    "such", "no", "only", "own", "same", "than", "too", "very", "just",
    "about", "up", "it", "its", "if", "this", "that", "these", "those",
    "what", "which", "who", "whom", "when", "where", "why", "how",
    "i", "me", "my", "we", "our", "you", "your", "he", "him", "his",
    "she", "her", "they", "them", "their",
})


def _extract_keywords(text: str, max_words: int = 6) -> str:
    """Extract keywords by removing stop words and punctuation."""
    # Remove punctuation except hyphens within words
    cleaned = re.sub(r"[^\w\s-]", " ", text.lower())
    words = [w for w in cleaned.split() if w not in _STOP_WORDS and len(w) > 1]
    return " ".join(words[:max_words])


class QueryBuilder:
    """Convert an Alert into candidate search queries for SM corroboration."""

    def build(self, alert: Alert) -> list[str]:
        """Return up to 3 candidate queries for the given alert."""
        if alert.source == SourceType.POLYMARKET:
            return self._polymarket_queries(alert)
        if alert.source == SourceType.GITHUB:
            return self._github_queries(alert)
        if alert.source == SourceType.HACKERNEWS:
            return []  # HN doesn't need to corroborate itself
        return []

    def _polymarket_queries(self, alert: Alert) -> list[str]:
        queries: list[str] = []
        data = alert.event.data

        # 1. event_slug → readable query
        event_slug = data.get("event_slug", "")
        if event_slug:
            queries.append(event_slug.replace("-", " "))

        # 2. question keywords
        question = data.get("question", "")
        if question:
            kw = _extract_keywords(question)
            if kw and kw not in queries:
                queries.append(kw)

        # 3. "polymarket" + truncated question for precise matching
        if question:
            pm_query = f"polymarket {question[:60]}"
            queries.append(pm_query)

        return queries[:3]

    def _github_queries(self, alert: Alert) -> list[str]:
        queries: list[str] = []
        data = alert.event.data

        full_name = data.get("full_name", alert.event.source_id)

        # 1. full_name (e.g. "openai/whisper") — works great on HN
        if full_name:
            queries.append(full_name)

        # 2. full_name without slash
        if full_name and "/" in full_name:
            queries.append(full_name.replace("/", " "))

        # 3. name + description keywords
        name = data.get("name", "")
        desc = data.get("description", "")
        if desc:
            kw = _extract_keywords(f"{name} {desc}")
            if kw and kw not in queries:
                queries.append(kw)

        return queries[:3]
