"""
Orchestrierung mit LangGraph statt Handschrift-Kontrollfluss.

Bewusste Designentscheidung: der eigentliche
Transaktionspfad (Onboarding, Vertrag, Order) bleibt eine EXPLIZITE
State-Machine, kein freier LLM-Agent, der selbst entscheidet, welches Tool
er als naechstes ruft. Grund: Auditierbarkeit und Vorhersagbarkeit sind bei
regulierten Finanzprozessen wichtiger als Flexibilitaet. LangGraph liefert
dafuer die passende native `interrupt()`-Funktion fuer das HITL-Gate
(Graph pausiert, State wird ueber den Checkpointer gehalten, ein Mensch
antwortet, der Graph laeuft mit `Command(resume=...)` an genau der Stelle
weiter).

Generatives Reasoning (ein echtes LLM) wird bewusst nur an EINER Stelle
eingesetzt, die dafuer geeignet ist: dem Parsen der freien Kundenanfrage
("Ich moechte nachhaltig 200 Euro im Monat sparen") in strukturierte
Praeferenzen. Ist kein OPENAI_API_KEY gesetzt, faellt das Parsing auf
eine einfache Keyword-Heuristik zurueck, damit die Demo trotzdem laeuft.
"""

import os
import re
from typing import TypedDict
from dotenv import load_dotenv

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from agent_client import MCPServerConnection

load_dotenv()  # .env fuer API_KEY's laden, falls vorhanden

class AgentState(TypedDict, total=False):
    raw_request: str
    customer_data: dict
    tax_id: str
    ident_channel: str
    risk_profile: dict
    preferences: dict
    product: dict
    session_id: str
    suitability: dict
    approved: bool
    contract: dict
    plan: dict
    summary: str
    missing_fields: list


def parse_customer_request(text: str) -> dict:
    """
    Extrahiert Praeferenzen aus der freien Kundenanfrage.
    Nutzt ein echtes LLM, falls OPENAI_API_KEY gesetzt ist -- sonst eine
    einfache Keyword-Heuristik als Fallback (fuer Demo/Test ohne API-Key).
    """
    print(f"Parsing Kundenanfrage mit LLM (OpenAI): {os.environ.get('OPENAI_API_KEY')}")

    if os.environ.get("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        from pydantic import BaseModel, Field

        class Preferences(BaseModel):
            esg: bool = Field(description="Kunde bevorzugt nachhaltige/ESG-Produkte")
            max_risk_class: int = Field(description="Maximale akzeptierte Risikoklasse, 1-7", ge=1, le=7)
            cost_sensitivity: str = Field(description="niedrig | mittel | hoch")
            savings_amount_eur: float = Field(description="Monatlicher Sparbetrag in Euro")

        #llm = ChatAnthropic(model="claude-sonnet-4-6", temperature=0)
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=os.environ.get("OPENAI_API_KEY"))
        print(f"LLM: {llm}")
        structured = llm.with_structured_output(Preferences)
        result: Preferences = structured.invoke(
            f"Extrahiere die Anlagepraeferenzen aus dieser Kundenanfrage: {text}"
        )
        return result.model_dump()

    # Fallback-Heuristik ohne LLM
    esg = bool(re.search(r"nachhaltig|esg|grün", text, re.IGNORECASE))
    amount_match = re.search(r"(\d+)\s*(?:€|eur|euro)", text, re.IGNORECASE)
    amount = float(amount_match.group(1)) if amount_match else 200.0
    return {
        "esg": esg,
        "max_risk_class": 5,
        "cost_sensitivity": "mittel",
        "savings_amount_eur": amount,
    }


def build_graph(discovery: MCPServerConnection, onboarding: MCPServerConnection,
                 trading: MCPServerConnection, event_cb):

    async def emit(step: str, agent: str, detail: dict | None = None):
        payload = {"step": step, "agent": agent, "detail": detail or {}}
        result = event_cb(payload)
        if hasattr(result, "__await__"):
            await result

    async def node_parse_request(state: AgentState):
        await emit("Parse Kundenanfrage in strukturierte Praeferenzen", "Kundenagent")
        prefs = parse_customer_request(state["raw_request"])
        await emit("Praeferenzen erkannt", "Kundenagent", prefs)
        return {"preferences": prefs}

    async def node_validate_customer_data(state: AgentState):
        await emit("Pruefe Vollstaendigkeit der Kundendaten (GwG/KYC-Pflichtfelder)", "Depot-Agent")
        result = await onboarding.call("check_required_fields", customer_data=state.get("customer_data", {}))
        if not result["complete"]:
            await emit(
                f"Angaben unvollstaendig -- fehlt: {', '.join(result['missing_fields'])}",
                "Depot-Agent", result,
            )
        else:
            await emit("Alle Pflichtangaben vorhanden", "Depot-Agent")
        return {"missing_fields": result["missing_fields"]}

    def route_after_validation(state: AgentState):
        return "abort_incomplete" if state["missing_fields"] else "research"

    async def node_abort_incomplete(state: AgentState):
        summary = (
            "Depoteroeffnung nicht gestartet -- folgende Pflichtangaben fehlen noch: "
            + ", ".join(state["missing_fields"]) + ". Bitte im Formular ergaenzen und erneut starten."
        )
        await emit("Abbruch vor Onboarding-Start", "Kundenagent", {"summary": summary})
        return {"summary": summary}

    async def node_research(state: AgentState):
        await emit("Pruefe Anbieteridentitaet", "Research-Agent")
        identity = await discovery.call("verify_provider_identity")
        if not identity.get("signature_valid"):
            await emit("Abbruch: Anbieteridentitaet nicht verifizierbar", "Research-Agent")
            raise RuntimeError("provider_identity_invalid")

        await emit("langgraph -> Vergleiche Produkte nach Kundenpraeferenzen", "Research-Agent")
        prefs = state["preferences"]
        ranked = await discovery.call(
            "compare_products",
            esg_preference=prefs["esg"],
            max_risk_class=prefs["max_risk_class"],
            cost_sensitivity=prefs["cost_sensitivity"],
        )
        product = ranked[0]
        await emit(f"Produkt gewaehlt: {product['name']} (Match {product['match_score']})", "Research-Agent", product)
        return {"product": product}

    async def node_start_onboarding(state: AgentState):
        await emit("Starte Onboarding-Session", "Depot-Agent")
        session = await onboarding.call("initiate_onboarding", customer_agent_id="agent-demo-001")
        return {"session_id": session["session_id"]}

    async def node_identity(state: AgentState):
        channel = state["ident_channel"]
        await emit(f"Waehle Ident-Kanal: {channel}", "Depot-Agent")
        if channel == "video_ident":
            info = await onboarding.call("request_video_ident", session_id=state["session_id"])
        elif channel == "eid":
            info = await onboarding.call("request_eid", session_id=state["session_id"])
        else:
            info = await onboarding.call("request_postident", session_id=state["session_id"])
        await onboarding.call(
            "submit_identity", session_id=state["session_id"], channel=channel, verification_ref="ref-demo-001"
        )
        await emit("Identitaet verifiziert", "Depot-Agent", info)
        return {}

    async def node_tax(state: AgentState):
        await onboarding.call("submit_tax_info", session_id=state["session_id"], tax_id=state["tax_id"])
        await emit("Steuerliche Angaben uebermittelt", "Depot-Agent")
        return {}

    async def node_suitability(state: AgentState):
        await emit("Fuehre Geeignetheitspruefung durch", "Depot-Agent")
        rp = state["risk_profile"]
        suitability = await onboarding.call(
            "run_suitability_check",
            session_id=state["session_id"],
            experience_years=rp["experience_years"],
            product_knowledge=rp["product_knowledge"],
            investment_horizon_years=rp["investment_horizon_years"],
            loss_tolerance=rp["loss_tolerance"],
        )
        await emit(f"Risiko-Score: {suitability['risk_score']}", "Depot-Agent", suitability)
        return {"suitability": suitability}

    def route_after_suitability(state: AgentState):
        return "human_review" if state["suitability"]["needs_human_review"] else "sign_contract"

    async def node_human_review(state: AgentState):
        # WICHTIG (LangGraph-Semantik): Code VOR interrupt() wird beim Resume
        # erneut ausgefuehrt, weil LangGraph den Knoten von vorne startet und
        # nur den interrupt()-Aufruf selbst "ueberspringt". Seiteneffekte vor
        # interrupt() muessen daher idempotent sein oder -- wie hier bewusst
        # in Kauf genommen -- duerfen unkritisch mehrfach laufen (ein erneutes
        # request_human_review ist harmlos). Fuer echte Produktionslogik mit
        # kritischen Seiteneffekten vor dem Interrupt: Status-Flag im State
        # pruefen, um Doppelausfuehrung zu verhindern.
        await onboarding.call(
            "request_human_review", session_id=state["session_id"],
            reason="Geringe Produktkenntnis / auffaelliger Risiko-Score",
        )
        await emit("HITL-Gate ausgeloest -- Graph pausiert (LangGraph interrupt)", "Mensch (HITL)", state["suitability"])
        decision = interrupt({"session_id": state["session_id"], "suitability": state["suitability"]})
        await emit(f"Freigabe: {'erteilt' if decision else 'verweigert'}", "Mensch (HITL)")
        return {"approved": bool(decision)}

    async def node_sign_contract(state: AgentState):
        approved = state.get("approved", True)
        contract = await onboarding.call("sign_contract", session_id=state["session_id"], approved=approved)
        if contract["contract_signed"]:
            await emit(f"Vertrag unterschrieben, Depot {contract['depot_id']}", "Depot-Agent", contract)
        else:
            await emit("Vertrag nicht unterschrieben", "Depot-Agent")
        return {"contract": contract}

    async def node_trading(state: AgentState):
        if not state["contract"]["contract_signed"]:
            return {"summary": None}
        prefs = state["preferences"]
        await emit(f"Richte Sparplan ein: {prefs['savings_amount_eur']} EUR/Monat", "Anleger-Agent")
        plan = await trading.call(
            "create_savings_plan",
            depot_id=state["contract"]["depot_id"],
            product_id=state["product"]["product_id"],
            amount_eur=prefs["savings_amount_eur"],
        )
        await emit(f"Sparplan bestaetigt: {plan['plan_id']}", "Anleger-Agent", plan)
        summary = (
            f"Depot {state['contract']['depot_id']} eroeffnet. Sparplan {plan['plan_id']} ueber "
            f"{plan['amount_eur']} EUR/Monat in '{state['product']['name']}' eingerichtet. "
            f"Erste Ausfuehrung: {plan['next_execution']}."
        )
        await emit("Zusammenfassung an Kunden", "Kundenagent", {"summary": summary})
        return {"plan": plan, "summary": summary}

    graph = StateGraph(AgentState)
    graph.add_node("parse_request", node_parse_request)
    graph.add_node("validate_customer_data", node_validate_customer_data)
    graph.add_node("abort_incomplete", node_abort_incomplete)
    graph.add_node("research", node_research)
    graph.add_node("start_onboarding", node_start_onboarding)
    graph.add_node("identity", node_identity)
    graph.add_node("tax", node_tax)
    graph.add_node("suitability", node_suitability)
    graph.add_node("human_review", node_human_review)
    graph.add_node("sign_contract", node_sign_contract)
    graph.add_node("trading", node_trading)

    graph.set_entry_point("parse_request")
    graph.add_edge("parse_request", "validate_customer_data")
    graph.add_conditional_edges(
        "validate_customer_data", route_after_validation,
        {"abort_incomplete": "abort_incomplete", "research": "research"},
    )
    graph.add_edge("abort_incomplete", END)
    graph.add_edge("research", "start_onboarding")
    graph.add_edge("research", "start_onboarding")
    graph.add_edge("start_onboarding", "identity")
    graph.add_edge("identity", "tax")
    graph.add_edge("tax", "suitability")
    graph.add_conditional_edges(
        "suitability", route_after_suitability, {"human_review": "human_review", "sign_contract": "sign_contract"}
    )
    graph.add_edge("human_review", "sign_contract")
    graph.add_edge("sign_contract", "trading")
    graph.add_edge("trading", END)

    return graph.compile(checkpointer=MemorySaver())

async def run_scenario_langgraph(customer_profile: dict, event_cb, hitl_cb, thread_id: str = "demo-1"):
    """
    Fuehrt das Szenario per LangGraph aus.
    """
    async with MCPServerConnection("discovery_server.py", sse_url=os.getenv("DISCOVERY_URL")) as discovery, \
               MCPServerConnection("onboarding_server.py", sse_url=os.getenv("ONBOARDING_URL")) as onboarding, \
               MCPServerConnection("trading_server.py", sse_url=os.getenv("TRADING_URL")) as trading:

        app = build_graph(discovery, onboarding, trading, event_cb)
        config = {"configurable": {"thread_id": thread_id}}

        initial_state: AgentState = {
            "raw_request": customer_profile["raw_request"],
            "customer_data": customer_profile.get("customer_data", {}),
            "tax_id": customer_profile["tax_id"],
            "ident_channel": customer_profile["ident_channel"],
            "risk_profile": customer_profile["risk_profile"],
        }
        
        result = await app.ainvoke(initial_state, config=config)

        # Die while-Schleife MUSS innerhalb des async-with-Blocks laufen!
        while isinstance(result, dict) and "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            approved = await hitl_cb(payload["session_id"], payload["suitability"])
            result = await app.ainvoke(Command(resume=approved), config=config)

        return result.get("summary") if isinstance(result, dict) else None