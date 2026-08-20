"""
FastAPI-Backend fuer die Praesentations-UI.

Startet den echten MCP-basierten Agentenflow (agent_client.run_scenario) und
streamt jeden Schritt live per WebSocket an die Browser-Seite. Beim HITL-Gate
wartet der Server auf eine Freigabe/Ablehnung, die per Button in der UI
gesendet wird.

Ausfuehren (aus dem Verzeichnis prototyp_v2/):
    python -m uvicorn web.app:app --reload
Dann im Browser: http://127.0.0.1:8000
"""

import os
import sys
from pathlib import Path

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))
from agent_client import run_scenario, MCPServerConnection 
from agent_langgraph import run_scenario_langgraph 

STATIC_DIR = Path(__file__).parent / "static"
VUE_DIR = Path(__file__).parent / "static-vue"

mcp_sessions = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Beim Start des Web-Containers Verbindungen aufbauen
    async with MCPServerConnection("discovery_server.py", sse_url=os.getenv("DISCOVERY_URL")) as discovery, \
               MCPServerConnection("onboarding_server.py", sse_url=os.getenv("ONBOARDING_URL")) as onboarding, \
               MCPServerConnection("trading_server.py", sse_url=os.getenv("TRADING_URL")) as trading:

        mcp_sessions["discovery"] = discovery
        mcp_sessions["onboarding"] = onboarding
        mcp_sessions["trading"] = trading

        # Optional: beim Start die verfügbaren Tools ausgeben (zum Debuggen)
        # for name, conn in [("discovery", discovery), ("onboarding", onboarding), ("trading", trading)]:
        #     tools_result = await conn.session.list_tools()
        #     print(f"[{name}] Available tools: {[t.name for t in tools_result.tools]}")

        yield  # App läuft hier

    # Beim Herunterfahren werden die Sessions sauber im SELBEN Task geschlossen
    mcp_sessions.clear()


app = FastAPI(title="Agentenbasierte Depoteroeffnung -- Demo", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/static-vue", StaticFiles(directory=VUE_DIR), name="static-vue")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/vue")
async def vue_index():
    return FileResponse(VUE_DIR / "index.html")


@app.get("/tools/{server_name}")
async def list_tools(server_name: str):
    """
    Gibt die verfügbaren Tools des angegebenen MCP-Servers zurück.
    Erwartete Werte: discovery, onboarding, trading
    """
    conn = mcp_sessions.get(server_name)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")
    try:
        tools_result = await conn.session.list_tools()
        return {"server": server_name, "tools": [t.name for t in tools_result.tools]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/tools")
async def list_all_tools():
    """
    Gibt die verfügbaren Tools aller MCP-Server auf einmal zurück.
    Struktur: { "discovery": [...], "onboarding": [...], "trading": [...] }
    """
    result = {}
    for name, conn in mcp_sessions.items():
        try:
            tools_result = await conn.session.list_tools()
            result[name] = [t.name for t in tools_result.tools]
        except Exception as exc:
            result[name] = {"error": str(exc)}
    return result


DEFAULT_PROFILE = {
    "tax_id": "12345678901",
    "preferences": {"esg": True, "max_risk_class": 5, "cost_sensitivity": "mittel"},
    "ident_channel": "video_ident",
    "risk_profile": {
        "experience_years": 2,
        "product_knowledge": "gering",
        "investment_horizon_years": 8,
        "loss_tolerance": "mittel",
    },
    "savings_amount_eur": 200,
    # Demo-Vorbelegung, damit ein Klick auf "Starten" weiterhin sofort
    # funktioniert -- im Formular sichtbar und editierbar.
    "customer_data": {
        "vorname": "Max",
        "nachname": "Mustermann",
        "geburtsdatum": "1990-05-14",
        "geburtsort": "Berlin",
        "staatsangehoerigkeit": "deutsch",
        "adresse": "Musterstraße 1, 10115 Berlin",
        "email": "max.mustermann@example.com",
        "telefon": "+49 151 00000000",
        "steuer_id": "12345678901",
        "steuerlich_ansaessig_in": "DE",
    },
}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        start_msg = await websocket.receive_json()
        profile = {**DEFAULT_PROFILE, **start_msg.get("profile", {})}

        async def event_cb(event: dict):
            await websocket.send_json({"type": "event", **event})

        async def hitl_cb(session_id: str, suitability: dict) -> bool:
            await websocket.send_json(
                {"type": "hitl_request", "session_id": session_id, "suitability": suitability}
            )
            response = await websocket.receive_json()
            return bool(response.get("approved"))

        summary = await run_scenario(profile, event_cb, hitl_cb)
        await websocket.send_json({"type": "done", "summary": summary})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # Demo-Robustheit: Fehler sichtbar an die UI melden
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass


@app.websocket("/ws-langgraph")
async def ws_langgraph_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        start_msg = await websocket.receive_json()
        raw_request = start_msg.get("raw_request", "Ich möchte nachhaltig 200 Euro im Monat sparen.")
        profile = {
            "raw_request": raw_request,
            "customer_data": {**DEFAULT_PROFILE["customer_data"], **start_msg.get("customer_data", {})},
            "tax_id": start_msg.get("customer_data", {}).get("steuer_id", DEFAULT_PROFILE["tax_id"]),
            "ident_channel": start_msg.get("ident_channel", DEFAULT_PROFILE["ident_channel"]),
            "risk_profile": {**DEFAULT_PROFILE["risk_profile"], **start_msg.get("risk_profile", {})},
        }

        async def event_cb(event: dict):
            await websocket.send_json({"type": "event", **event})

        async def hitl_cb(session_id: str, suitability: dict) -> bool:
            await websocket.send_json(
                {"type": "hitl_request", "session_id": session_id, "suitability": suitability}
            )
            response = await websocket.receive_json()
            return bool(response.get("approved"))

        summary = await run_scenario_langgraph(profile, event_cb, hitl_cb)
        await websocket.send_json({"type": "done", "summary": summary})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass

# --- Custom OpenAPI Generator für WebSockets & Swagger UI ---
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    # standard OpenAPI-Schema aus bestehenden Routes aufbauen
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # WebSockets manuell in das OpenAPI-Pfade-Verzeichnis einspeisen
    openapi_schema["paths"]["/ws"] = {
        "get": {
            "summary": "Imperativer Agenten-Workflow (WebSocket)",
            "description": (
                "Echtzeit-Verbindung für den klassischen/imperativen Agenten-Szenario-Flow. "
                "Sendet Events (`type: event`), empfängt HITL-Freigaben und schließt ab."
            ),
            "tags": ["WebSockets (Echtzeit-Streaming)"],
            "responses": {
                "101": {"description": "Switching Protocols (WebSocket eingerichtet)"}
            },
        }
    }

    openapi_schema["paths"]["/ws-langgraph"] = {
        "get": {
            "summary": "LangGraph State Machine Agent (WebSocket)",
            "description": (
                "Echtzeit-Verbindung für den **LangGraph State-Machine**-Workflow. "
                "Unterstützt `interrupt()`-Pausen für das HITL-Gate und `Command(resume=...)` bei Freigabe."
            ),
            "tags": ["WebSockets (Echtzeit-Streaming)"],
            "responses": {
                "101": {"description": "Switching Protocols (WebSocket eingerichtet)"}
            },
        }
    }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi