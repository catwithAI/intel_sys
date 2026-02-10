from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("/{source}")
async def list_alerts(source: str, request: Request, limit: int = 20, offset: int = 0):
    """Get latest alerts for a source."""
    redis = request.app.state.redis
    key = f"alerts:{source}"

    if not await redis.exists(key):
        return {"source": source, "alerts": [], "total": 0}

    total = await redis.llen(key)
    raw_alerts = await redis.lrange(key, offset, offset + limit - 1)
    alerts = [json.loads(a) for a in raw_alerts]

    return {"source": source, "alerts": alerts, "total": total}


@router.get("/{source}/{alert_id}")
async def get_alert(source: str, alert_id: str, request: Request):
    """Get a single alert by ID."""
    redis = request.app.state.redis
    key = f"alerts:{source}"

    raw_alerts = await redis.lrange(key, 0, -1)
    for raw in raw_alerts:
        alert = json.loads(raw)
        if alert.get("id") == alert_id:
            return alert

    raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
