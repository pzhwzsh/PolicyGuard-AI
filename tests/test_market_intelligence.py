from pathlib import Path

from policyguard.application.market_intelligence import MarketIntelligenceRetriever


ROOT = Path(__file__).parents[1]
DATASET = ROOT / "data/market-intelligence/eu-us-hvac.json"


def test_eu_hvac_query_returns_verified_regulatory_opportunity() -> None:
    results = MarketIntelligenceRetriever.from_path(DATASET).search(
        "空调低GWP制冷剂和F-gas要求", ["EU"], category="空调"
    )
    assert results
    assert results[0]["id"] == "eu-hvac-fgas-2024"
    assert results[0]["verification_status"] == "official_source_verified"
    assert "universally bans outdoor" in results[0]["caveat"]


def test_us_heat_pump_query_does_not_return_eu_records() -> None:
    results = MarketIntelligenceRetriever.from_path(DATASET).search(
        "heat pump tax credit ENERGY STAR", ["US"], category="hvac"
    )
    assert results[0]["id"] == "us-heat-pump-tax-credit"
    assert {item["jurisdiction"] for item in results} == {"US"}


def test_non_matching_copy_does_not_inject_market_opportunities() -> None:
    results = MarketIntelligenceRetriever.from_path(DATASET).search(
        "普通棉质短袖", ["EU", "US"], category="服装"
    )
    assert results == []
