from __future__ import annotations

import importlib

import pytest


def _build_alerts():
    app_models = importlib.import_module("app.models")
    event = app_models.Event(
        source=app_models.SourceType.DEFENSE,
        source_id="breakingdefense:1",
        data={
            "title": "Hypersonic missile update",
            "content": "Body summary",
            "url": "https://example.com/post",
        },
        metadata={
            "site_id": "breakingdefense",
            "site_name": "Breaking Defense",
            "country": "US",
            "canonical_url": "https://example.com/post",
        },
    )
    alert = app_models.Alert(
        source=app_models.SourceType.DEFENSE,
        rule_name="ingest_defense_news",
        title="[DEFENSE] Hypersonic missile update",
        event=event,
    )
    return alert


def test_task_12_rule_pipeline():
    rules_mod = importlib.import_module("app.rules.defense_rules")
    assert hasattr(rules_mod, "ingest_defense_news")
    assert callable(rules_mod.ingest_defense_news)
    assert hasattr(rules_mod, "_score_to_severity")

    registry_mod = importlib.import_module("app.engine.registry")
    assert "ingest_defense_news" in registry_mod.rule_registry.rules


def test_task_13_app_integration():
    # Verify RuleContext has app_state
    context_mod = importlib.import_module("app.engine.context")
    from dataclasses import fields
    field_names = {f.name for f in fields(context_mod.RuleContext)}
    assert "app_state" in field_names

    # Verify defense storage and health modules are importable
    importlib.import_module("app.defense.storage")
    importlib.import_module("app.defense.health")

    # Read main.py source to verify app_state is passed
    from tests.defense_tasks.conftest import read_project_text
    main_src = read_project_text("app/main.py")
    assert "app_state" in main_src
    assert "defense_app_state" in main_src
    assert "pg_pool" in main_src

    debug_src = read_project_text("app/routes/debug.py")
    assert "defense_health" in debug_src
    assert "defense_runs" in debug_src


@pytest.mark.asyncio
async def test_task_14_feishu_defense_cards_contract():
    feishu_mod = importlib.import_module("app.delivery.feishu")
    alert = _build_alerts()

    delivery = feishu_mod.FeishuWebhookDelivery("https://example.com/webhook")
    payload = delivery._format_alert(alert)
    body_str = str(payload)
    assert "[DEFENSE]" in payload["card"]["header"]["title"]["content"]
    assert "Breaking Defense" in body_str
    assert "US" in body_str
    assert "https://example.com/post" in body_str

    digest = delivery._format_defense_digest_card([alert, alert.model_copy(update={"id": "2"})])
    assert "Hypersonic missile update" in str(digest)
    await delivery.close()


def test_milestone_5_checkpoint():
    """Milestone 5: defense rule + app integration + Feishu cards all wired."""
    import importlib

    # Rule registered
    registry_mod = importlib.import_module("app.engine.registry")
    assert "ingest_defense_news" in registry_mod.rule_registry.rules

    # RuleContext has app_state
    context_mod = importlib.import_module("app.engine.context")
    from dataclasses import fields
    assert "app_state" in {f.name for f in fields(context_mod.RuleContext)}

    # Feishu defense card works end-to-end
    feishu_mod = importlib.import_module("app.delivery.feishu")
    alert = _build_alerts()
    delivery = feishu_mod.FeishuWebhookDelivery("https://example.com/webhook")
    payload = delivery._format_alert(alert)
    assert "[DEFENSE]" in payload["card"]["header"]["title"]["content"]

    # Defense storage and health importable
    importlib.import_module("app.defense.storage")
    importlib.import_module("app.defense.health")
