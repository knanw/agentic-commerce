"""
Echter MCP-Server (stdio-Transport) fuer Discovery-Funktionen.

Startet nicht von Hand -- wird vom agent_client.py als Subprozess gespawnt
und ueber das MCP-Protokoll (JSON-RPC ueber stdio) angesprochen.
"""
import os
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
mcp = FastMCP("discovery-server",transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
)

PRODUCTS = [
    {
        "product_id": "etf-msci-world-001",
        "name": "Nachhaltiger MSCI World ETF",
        "category": "etf_sparplan",
        "ter": 0.20,
        "min_savings_rate_eur": 25,
        "risk_class": 4,
        "esg_rated": True,
        "asset_class": "Aktien global",
    },
    {
        "product_id": "etf-em-markets-002",
        "name": "Emerging Markets ETF",
        "category": "etf_sparplan",
        "ter": 0.35,
        "min_savings_rate_eur": 25,
        "risk_class": 6,
        "esg_rated": False,
        "asset_class": "Aktien Schwellenlaender",
    },
    {
        "product_id": "etf-euro-bonds-003",
        "name": "Euro-Staatsanleihen ETF",
        "category": "etf_sparplan",
        "ter": 0.15,
        "min_savings_rate_eur": 25,
        "risk_class": 2,
        "esg_rated": False,
        "asset_class": "Anleihen Euro",
    },
    {
        "product_id": "etf-msci-world-esg-004",
        "name": "MSCI World SRI ETF",
        "category": "etf_sparplan",
        "ter": 0.18,
        "min_savings_rate_eur": 25,
        "risk_class": 4,
        "esg_rated": True,
        "asset_class": "Aktien global",
    },
]


@mcp.tool()
def list_products(category: str = "etf_sparplan") -> list[dict]:
    """Listet alle Produkte einer Kategorie mit Konditionen."""
    return [p for p in PRODUCTS if p["category"] == category]


@mcp.tool()
def verify_provider_identity() -> dict:
    """Liefert signierte Anbieter-Metadaten als Anti-Phishing-Nachweis."""
    return {
        "provider_name": "Beispiel Fondsgesellschaft AG",
        "provider_id": "DE-FONDS-00042",
        "signature_valid": True,
        "bafin_license": "BaFin-Reg-Nr. 123456",
    }


@mcp.tool()
def compare_products(
    esg_preference: bool = False,
    max_risk_class: int = 7,
    cost_sensitivity: str = "mittel",
) -> list[dict]:
    """
    Vergleicht und rankt Produkte nach Kundenpraeferenzen.

    esg_preference: Kunde bevorzugt nachhaltige Produkte
    max_risk_class: maximale akzeptierte Risikoklasse (1-7)
    cost_sensitivity: "niedrig" | "mittel" | "hoch" -- wie stark die TER gewichtet wird
    """
    cost_weight = {"niedrig": 0.2, "mittel": 0.5, "hoch": 0.9}.get(cost_sensitivity, 0.5)

    candidates = [p for p in PRODUCTS if p["risk_class"] <= max_risk_class]

    def score(p: dict) -> float:
        # Niedrigere TER ist besser -> in Score umrechnen (0..1, 0.15% ~ 1.0, 0.40%+ ~ 0.0)
        cost_score = max(0.0, 1.0 - (p["ter"] - 0.15) / 0.25)
        esg_score = 1.0 if (esg_preference and p["esg_rated"]) else (0.5 if not esg_preference else 0.0)
        return cost_weight * cost_score + (1 - cost_weight) * esg_score

    ranked = sorted(candidates, key=score, reverse=True)
    return [
        {**p, "match_score": round(score(p), 2)}
        for p in ranked
    ]


@mcp.tool()
async def search_market_depots(query: str) -> list[dict]:
    """Sucht im Web nach aktuellen Depot-Konditionen von Wettbewerbern."""
    # 1. Ruft z. B. eine Search API (Tavily, SerpAPI) auf
    # 2. Extrahiert Konditionen (Kosten, Gebühren, Aktionen) per LLM
    # 3. Gibt strukturierte Liste von externen Anbietern zurück
    pass

@mcp.tool()
def get_own_depot_conditions() -> dict:
    """Liefert die tagesaktuellen Konditionen des EIGENEN Depots aus der internen DB."""
    return {
        "provider": "MeinFintech Bank",
        "depot_fee": 0.0,
        "trade_cost_flat": 1.0,
        "esg_focus": True
    }

@mcp.tool()
def compare_all_available_depots(user_preferences: dict) -> list[dict]:
    """Führt interne Konditionen und extern gescrapte Daten zusammen und berechnet das Ranking."""
    # Kombiniert interne DB + externe Suchergebnisse
    pass


if __name__ == "__main__":
    #mcp.run(transport="stdio")
    mcp.run()