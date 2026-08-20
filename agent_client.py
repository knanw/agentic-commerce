"""
Kundenseitige Agenten -- Version mit echtem MCP-Protokoll.

Im Unterschied zur einfachen Prototyp-Version (siehe ../prototyp/) verbindet
sich hier jeder Agent per stdio ueber das offizielle MCP-Python-SDK zu einem
echten, als Subprozess laufenden MCP-Server (mcp_servers/*.py). Tool-Aufrufe
laufen als echte JSON-RPC-Nachrichten ueber das Protokoll, nicht als direkte
Python-Funktionsaufrufe.

event_cb(dict) wird fuer jeden Schritt aufgerufen (fuer Logging/UI).
hitl_cb(session_id, suitability) -> bool wird bei Bedarf awaited und muss
True (Freigabe) oder False (Ablehnung) liefern -- z.B. per Konsoleneingabe
oder per Websocket-Antwort aus einer Web-UI.
"""

import asyncio
import contextlib
import json
from logging import log
import os
import sys
from pathlib import Path

from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

from mcp.client.streamable_http import streamable_http_client

SERVERS_DIR = Path(__file__).parent / "mcp_servers"

# Default-Ports/URLs fuer den HTTP-Modus (Docker/Cloud). Im stdio-Modus
# (lokale Entwicklung/Interview-Demo) irrelevant, da Subprozesse gespawnt werden.
# HTTP_SERVER_CONFIG = {
#     "discovery_server.py": {"url_env": "MCP_DISCOVERY_URL", "default_url": "http://localhost:8001/mcp"},
#     "onboarding_server.py": {"url_env": "MCP_ONBOARDING_URL", "default_url": "http://localhost:8002/mcp"},
#     "trading_server.py": {"url_env": "MCP_TRADING_URL", "default_url": "http://localhost:8003/mcp"},
# }

HTTP_SERVER_CONFIG = {
    "discovery_server.py": {
        "url_env": "DISCOVERY_URL",
        "default_url": "http://discovery:8001/sse",
    },
    "onboarding_server.py": {
        "url_env": "ONBOARDING_URL",
        "default_url": "http://onboarding:8002/sse",
    },
    "trading_server.py": {
        "url_env": "TRADING_URL",
        "default_url": "http://trading:8003/sse",
    },
}


# def parse_tool_result(result):
#     """Normalisiert MCP-Tool-Ergebnisse (dict oder Liste) zu reinen Python-Objekten."""
#     if result.structuredContent is not None:
#         sc = result.structuredContent
#         return sc.get("result", sc)
#     parts = [json.loads(c.text) for c in result.content if c.type == "text"]
#     return parts[0] if len(parts) == 1 else parts


def parse_tool_result(result):
    parts = []
    for c in result.content:
        if c.type == "text":
            try:
                parts.append(json.loads(c.text))
            except (json.JSONDecodeError, TypeError):
                # Falls der Rückgabewert normaler Text ist (kein JSON)
                parts.append(c.text)
    
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else parts

class MCPServerConnection:
    def __init__(self, server_script_name: str, sse_url: str = None):
        self.server_script_name = server_script_name
        self.sse_url = sse_url
        self._stack = AsyncExitStack()
        self.session = None

    async def __aenter__(self):
        # 1. Option: SSE-Verbindung (z. B. im Docker-Netzwerk)
        if self.sse_url:
            print(f"Starte MCP-Server im SSE-Modus: {self.server_script_name}")

            read_stream, write_stream, *_ = await self._stack.enter_async_context(
                sse_client(self.sse_url)
            )
            self.session = await self._stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
        
        # 2. Option: STDIO-Subprozess (Lokal auf deinem Rechner)
        else:
            print(f"Starte MCP-Server im STDIO-Modus: {self.server_script_name}")
            script_path = os.path.join("mcp_servers", self.server_script_name)
            server_params = StdioServerParameters(
                command=sys.executable,  # Nutzt das aktuelle Python-Executable
                args=[script_path],
                env=None
            )
            read_stream, write_stream = await self._stack.enter_async_context(
                stdio_client(server_params)
            )
            self.session = await self._stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )

        await self.session.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._stack.aclose()

    async def call(self, tool_name: str, **kwargs):
        """Führt ein MCP-Tool sicher über call_tool aus und parst das Ergebnis."""
        result = await self.session.call_tool(tool_name, arguments=kwargs)
        return parse_tool_result(result)

async def run_scenario(customer_profile: dict, event_cb, hitl_cb):
    async def emit(step: str, agent: str, detail: dict | None = None):
        payload = {"step": step, "agent": agent, "detail": detail or {}}
        res = event_cb(payload)
        if asyncio.iscoroutine(res):
            await res

    # ALLES muss innerhalb DIESES Blocks stehen:
    async with MCPServerConnection("discovery_server.py", sse_url=os.getenv("DISCOVERY_URL")) as discovery, \
               MCPServerConnection("onboarding_server.py", sse_url=os.getenv("ONBOARDING_URL")) as onboarding, \
               MCPServerConnection("trading_server.py", sse_url=os.getenv("TRADING_URL")) as trading:

        # --- Research-Agent ---
        await emit("Pruefe Anbieteridentitaet", "Research-Agent")
        identity = await discovery.call("verify_provider_identity")
        if not identity.get("signature_valid"):
            await emit("Abbruch: Anbieteridentitaet nicht verifizierbar", "Research-Agent")
            return None

        await emit("local -> Vergleiche Produkte nach Kundenpraeferenzen", "Research-Agent")
        ranked = await discovery.call(
            "compare_products",
            esg_preference=customer_profile["preferences"]["esg"],
            max_risk_class=customer_profile["preferences"]["max_risk_class"],
            cost_sensitivity=customer_profile["preferences"]["cost_sensitivity"],
        )
        product = ranked[0]
        await emit(f"Produkt gewaehlt: {product['name']} (Match {product['match_score']})", "Research-Agent", product)

        # --- Depot-Agent ---
        await emit("Starte Onboarding-Session", "Depot-Agent")
        session = await onboarding.call("initiate_onboarding", customer_agent_id="agent-demo-001")

        if isinstance(session, str):
            session = json.loads(session)

        session_id = session["session_id"]

        channel = customer_profile["ident_channel"]
        await emit(f"Waehle Ident-Kanal: {channel}", "Depot-Agent")
        if channel == "video_ident":
            channel_info = await onboarding.call("request_video_ident", session_id=session_id)
        else:
            channel_info = await onboarding.call("request_postident", session_id=session_id)
        await emit("Kanal-Details erhalten", "Depot-Agent", channel_info)

        await onboarding.call("submit_identity", session_id=session_id, channel=channel, verification_ref="ref-demo-001")
        await emit("Identitaet verifiziert", "Depot-Agent")

        await onboarding.call("submit_tax_info", session_id=session_id, tax_id=customer_profile["tax_id"])
        await emit("Steuerliche Angaben uebermittelt", "Depot-Agent")

        await emit("Fuehre Geeignetheitspruefung durch", "Depot-Agent")
        rp = customer_profile["risk_profile"]
        suitability = await onboarding.call(
            "run_suitability_check",
            session_id=session_id,
            experience_years=rp["experience_years"],
            product_knowledge=rp["product_knowledge"],
            investment_horizon_years=rp["investment_horizon_years"],
            loss_tolerance=rp["loss_tolerance"],
        )
        await emit(f"Risiko-Score: {suitability['risk_score']}", "Depot-Agent", suitability)

        approved = True
        if suitability["needs_human_review"]:
            await onboarding.call(
                "request_human_review",
                session_id=session_id,
                reason="Geringe Produktkenntnis / auffaelliger Risiko-Score")
            await emit("HITL-Gate ausgeloest -- warte auf menschliche Freigabe", "Mensch (HITL)", suitability)
            approved = await hitl_cb(session_id, suitability)
            await emit(f"Freigabe: {'erteilt' if approved else 'verweigert'}", "Mensch (HITL)")
        else:
            await emit("Kein HITL noetig -- Score im unkritischen Bereich", "Depot-Agent")

        contract = await onboarding.call("sign_contract", session_id=session_id, approved=approved)
        if not contract["contract_signed"]:
            await emit("Abbruch: Vertrag nicht unterschrieben", "Depot-Agent")
            return None
        await emit(f"Vertrag unterschrieben, Depot {contract['depot_id']}", "Depot-Agent", contract)

        # --- Anleger-Agent ---
        await emit(f"Richte Sparplan ein: {customer_profile['savings_amount_eur']} EUR/Monat", "Anleger-Agent")
        plan = await trading.call(
            "create_savings_plan",
            depot_id=contract["depot_id"],
            product_id=product["product_id"],
            amount_eur=customer_profile["savings_amount_eur"],
        )
        await emit(f"Sparplan bestaetigt: {plan['plan_id']}", "Anleger-Agent", plan)

        summary = (
            f"Depot {contract['depot_id']} eroeffnet. Sparplan {plan['plan_id']} ueber "
            f"{plan['amount_eur']} EUR/Monat in '{product['name']}' eingerichtet. "
            f"Erste Ausfuehrung: {plan['next_execution']}."
        )
        await emit("Zusammenfassung an Kunden", "Kundenagent", {"summary": summary})
        return summary
