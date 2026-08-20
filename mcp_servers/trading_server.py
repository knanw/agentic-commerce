"""
Echter MCP-Server (stdio-Transport) fuer Trading: Sparplan- und Orderverwaltung.
"""
import os
import sys
import uuid

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
mcp = FastMCP("trading-server",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
)


@mcp.tool()
def create_savings_plan(depot_id: str, product_id: str, amount_eur: float, interval: str = "monthly") -> dict:
    """Legt einen Sparplan auf einem bestehenden Depot an."""
    plan_id = "PLAN-" + str(uuid.uuid4())[:8].upper()
    return {
        "plan_id": plan_id,
        "depot_id": depot_id,
        "product_id": product_id,
        "amount_eur": amount_eur,
        "interval": interval,
        "status": "confirmed",
        "next_execution": "2026-08-01",
    }


@mcp.tool()
def place_order(depot_id: str, product_id: str, amount_eur: float, order_type: str = "buy") -> dict:
    """Erteilt eine Einzelorder auf einem bestehenden Depot."""
    order_id = "ORDER-" + str(uuid.uuid4())[:8].upper()
    return {
        "order_id": order_id,
        "depot_id": depot_id,
        "product_id": product_id,
        "amount_eur": amount_eur,
        "order_type": order_type,
        "status": "executed",
    }


if __name__ == "__main__":
    #mcp.run(transport="stdio")
    mcp.run()