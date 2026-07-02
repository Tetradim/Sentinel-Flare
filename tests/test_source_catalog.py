from darkpool.source_catalog import build_trade_confirmation_plan, list_market_information_sources


def test_source_catalog_marks_finra_as_context_and_live_feeds_as_confirmation():
    sources = list_market_information_sources()
    by_id = {source.id: source for source in sources}

    assert by_id["finra_otc_transparency"].role == "darkpool_context"
    assert by_id["finra_otc_transparency"].cadence == "delayed"
    assert "not a real-time execution confirmation" in by_id["finra_otc_transparency"].limitations[0]
    assert by_id["sip_nbbo"].role == "price_confirmation"
    assert by_id["sip_nbbo"].priority == "required"
    assert by_id["opra_options"].role == "options_confirmation"
    assert by_id["trading_halts"].role == "risk_blocker"


def test_trade_confirmation_plan_separates_available_context_from_missing_confirmation_sources():
    plan = build_trade_confirmation_plan(active_provider="finra", configured_providers=["finra"])
    by_id = {source.id: source for source in plan.sources}

    assert by_id["finra_otc_transparency"].status == "available"
    assert by_id["sip_nbbo"].status == "missing"
    assert by_id["opra_options"].status == "missing"
    assert by_id["trading_halts"].status == "missing"
    assert plan.available_confirmation_weight == 0
    assert "real-time price/NBBO" in plan.recommended_next_sources[0]
    assert "context source available" in plan.summary


def test_trade_confirmation_plan_recommends_required_halt_and_news_sources():
    plan = build_trade_confirmation_plan(active_provider="finra", configured_providers=["finra"])

    assert any(
        "Nasdaq Trade Halt RSS" in recommendation and "halt/LULD" in recommendation
        for recommendation in plan.recommended_next_sources
    )
    assert any(
        "SEC EDGAR" in recommendation and "material-news" in recommendation
        for recommendation in plan.recommended_next_sources
    )


def test_trade_confirmation_plan_reports_role_level_confirmation_coverage():
    plan = build_trade_confirmation_plan(active_provider="finra", configured_providers=["finra"])
    coverage = {item.role: item for item in plan.coverage}

    assert coverage["darkpool_context"].status == "met"
    assert coverage["price_confirmation"].status == "missing"
    assert coverage["liquidity_confirmation"].status == "missing"
    assert coverage["risk_blocker"].status == "missing"
    assert coverage["news_context"].status == "missing"
    assert coverage["options_confirmation"].required is False
    assert plan.required_coverage_complete is False
    assert "sip_nbbo" in coverage["price_confirmation"].missing_source_ids


def test_configured_live_market_data_marks_only_matching_confirmation_roles_met():
    plan = build_trade_confirmation_plan(
        active_provider="demo",
        configured_providers=["polygon", "intrinio"],
    )
    coverage = {item.role: item for item in plan.coverage}

    assert coverage["price_confirmation"].status == "met"
    assert coverage["liquidity_confirmation"].status == "met"
    assert coverage["options_confirmation"].status == "met"
    assert coverage["risk_blocker"].status == "missing"
    assert coverage["news_context"].status == "missing"
    assert plan.required_coverage_complete is False


def test_configured_halt_and_edgar_sources_complete_required_confirmation_coverage():
    plan = build_trade_confirmation_plan(
        active_provider="demo",
        configured_providers=["polygon", "nasdaq_halts", "sec_edgar"],
    )
    coverage = {item.role: item for item in plan.coverage}
    by_id = {source.id: source for source in plan.sources}

    assert by_id["trading_halts"].status == "configured"
    assert by_id["news_events"].status == "configured"
    assert coverage["price_confirmation"].status == "met"
    assert coverage["liquidity_confirmation"].status == "met"
    assert coverage["risk_blocker"].status == "met"
    assert coverage["news_context"].status == "met"
    assert coverage["options_confirmation"].status == "missing"
    assert plan.required_coverage_complete is True
    assert "required confirmations complete" in plan.summary


def test_confirmation_plan_caps_operator_weight_at_full_coverage():
    plan = build_trade_confirmation_plan(
        active_provider="demo",
        configured_providers=["polygon", "intrinio", "nasdaq_halts", "sec_edgar"],
    )

    assert plan.available_confirmation_weight == 1.0
    assert plan.missing_confirmation_weight == 0.0
    assert "1.00 confirmation weight configured, 0.00 missing" in plan.summary


def test_information_sources_endpoint_does_not_mark_finra_available_for_demo_workflow():
    from fastapi.testclient import TestClient

    import server

    client = TestClient(server.app)
    response = client.get("/darkpool/information-sources?active_provider=demo")

    assert response.status_code == 200, response.text
    plan = response.json()["confirmation_plan"]
    by_id = {source["id"]: source for source in plan["sources"]}
    assert by_id["finra_otc_transparency"]["status"] == "missing"
    assert "no darkpool context source available" in plan["summary"]
    coverage = {item["role"]: item for item in plan["coverage"]}
    assert coverage["price_confirmation"]["status"] == "missing"
    assert plan["required_coverage_complete"] is False


def test_information_sources_endpoint_marks_finra_available_for_finra_workflow():
    from fastapi.testclient import TestClient

    import server

    client = TestClient(server.app)
    response = client.get("/darkpool/information-sources?active_provider=finra")

    assert response.status_code == 200, response.text
    plan = response.json()["confirmation_plan"]
    by_id = {source["id"]: source for source in plan["sources"]}
    assert by_id["finra_otc_transparency"]["status"] == "available"
    assert "context source available" in plan["summary"]


def test_configured_market_providers_include_halt_and_edgar_adapters_from_environment(monkeypatch):
    from routes.darkpool_routes import configured_market_providers

    monkeypatch.setenv("NASDAQ_HALTS_RSS_ENABLED", "true")
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "sentinel-flare test@example.com")

    configured = configured_market_providers("demo")

    assert "nasdaq_halts" in configured
    assert "sec_edgar" in configured
