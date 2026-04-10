from __future__ import annotations

from app.defense.models import NormalizedEvent, SourceSpec

TIER_BONUS = {1: 0.2, 2: 0.1, 3: 0.0, 4: -0.1}


class Scorer:
    """Two-stage filter and scorer for defense events."""

    def stage1_filter(
        self, events: list[NormalizedEvent], specs_map: dict[str, SourceSpec]
    ) -> list[NormalizedEvent]:
        """Stage 1: hard filter — quality, blacklist (no exemption), junk (whitelist exemption)."""
        result: list[NormalizedEvent] = []
        for event in events:
            spec = specs_map.get(event.site_id)
            if not spec:
                result.append(event)
                continue

            # Quality floor
            if event.extraction_quality < 0.4:
                continue

            title_lower = event.title.lower()

            # title_blacklist: hard drop, no whitelist exemption
            if any(bl.lower() in title_lower for bl in spec.filters.title_blacklist):
                continue

            # junk_patterns: soft drop, whitelist exemption
            junk_hit = any(jp.lower() in title_lower for jp in spec.filters.junk_patterns)
            if junk_hit:
                whitelist_hit = any(wl.lower() in title_lower for wl in spec.filters.title_whitelist)
                if not whitelist_hit:
                    continue

            result.append(event)
        return result

    def stage2_score(
        self, events: list[NormalizedEvent], specs_map: dict[str, SourceSpec]
    ) -> list[NormalizedEvent]:
        """Stage 2: compute pre_score per design formula."""
        scored: list[NormalizedEvent] = []
        for event in events:
            spec = specs_map.get(event.site_id)
            score = 0.0

            title_lower = event.title.lower()

            if spec:
                # Whitelist keyword match: +0.3
                if any(wl.lower() in title_lower for wl in spec.filters.title_whitelist):
                    score += 0.3

                # Junk pattern match (survived Stage 1 via whitelist exemption): -0.4
                if any(jp.lower() in title_lower for jp in spec.filters.junk_patterns):
                    score -= 0.4

                # Credibility: +credibility * 0.2
                score += spec.credibility * 0.2

                # Extraction quality: +quality * 0.1
                score += event.extraction_quality * 0.1

                # Authority tier bonus
                score += TIER_BONUS.get(spec.authority_tier, 0.0)
            else:
                score += event.source_weight * 0.2 + event.extraction_quality * 0.1

            event.pre_score = max(0.0, score)
            scored.append(event)
        return scored

    def topk(self, events: list[NormalizedEvent], k: int) -> list[NormalizedEvent]:
        """Return top-k events sorted by pre_score descending."""
        return sorted(events, key=lambda e: e.pre_score, reverse=True)[:k]
