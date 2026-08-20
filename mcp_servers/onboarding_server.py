"""
Echter MCP-Server (stdio-Transport) fuer Onboarding: Identitaet, Steuerangaben,
Geeignetheitspruefung, HITL-Gate, Vertragsabschluss.
"""
import os
import sys
import time
import uuid

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
mcp = FastMCP("onboarding-server",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
)

# In-Memory-Sessionspeicher (Prototyp -- in echt: persistente, auditierbare Speicherung)
SESSIONS: dict[str, dict] = {}


@mcp.tool()
def initiate_onboarding(customer_agent_id: str) -> dict:
    """Startet eine neue Onboarding-Session fuer einen Kundenagenten."""
    session_id = str(uuid.uuid4())[:8]
    SESSIONS[session_id] = {"customer_agent_id": customer_agent_id, "status": "started"}
    return {"session_id": session_id, "status": "started"}


# Pflichtfelder, abgeleitet aus der Ist-Analyse (N26/comdirect-Recherche):
# persoenliche Daten + Kontaktdaten (Basis-KYC) und Steuerdaten (GwG/steuerliche
# Meldepflicht). Wertpapierkenntnisse/Depothandelsstufe kommen separat aus
# dem Suitability-Check (run_suitability_check), da sie fachlich zur
# Geeignetheitspruefung gehoeren, nicht zur reinen Identifizierung.
REQUIRED_CUSTOMER_FIELDS = [
    "vorname", "nachname", "geburtsdatum", "geburtsort", "staatsangehoerigkeit",
    "adresse", "email", "telefon", "steuer_id", "steuerlich_ansaessig_in",
]


@mcp.tool()
def check_required_fields(customer_data: dict) -> dict:
    """
    Prueft, ob alle fuer die Depoteroeffnung noetigen Pflichtangaben vorhanden
    sind (Basis-KYC + Steuerdaten). Liefert fehlende Felder zurueck, statt
    blind mit unvollstaendigen Daten weiterzumachen -- das ist im echten
    Prozess exakt die Stelle, an der comdirect/N26 den Nutzer im Formular
    stoppen wuerden.
    """
    missing = [f for f in REQUIRED_CUSTOMER_FIELDS if not str(customer_data.get(f, "")).strip()]
    return {"complete": len(missing) == 0, "missing_fields": missing}


@mcp.tool()
def list_ident_channels() -> list[dict]:
    """
    Listet alle verfuegbaren Ident-Kanaele mit realistischen (benannten)
    Anbietern -- Grundlage fuer die Kanalwahl des Depot-Agenten und fuer die
    Build-vs-Buy-Diskussion (Kanal-Orchestrierung: Build: Verfahren
    selbst: Buy).
    """
    return [
        {
            "channel": "video_ident",
            "provider": "IDnow / WebID (Buy, externer Dienstleister)",
            "eta_minutes": 15,
            "requires_live_slot": True,
            "geeignet_fuer": "Standardfall, schnellste Option",
        },
        {
            "channel": "postident",
            "provider": "Deutsche Post PostIdent (Buy)",
            "eta_days": 3,
            "requires_live_slot": False,
            "geeignet_fuer": "Kunde ohne Termin-/Kameramoeglichkeit",
        },
        {
            "channel": "eid",
            "provider": "AusweisApp2 / Online-Ausweisfunktion (eID, Buy: Bundesdruckerei-Infrastruktur)",
            "eta_minutes": 5,
            "requires_live_slot": False,
            "geeignet_fuer": "Kunde mit Personalausweis + eID-PIN aktiviert -- schnellste, vollautomatisierbare Option",
        },
    ]


@mcp.tool()
def request_video_ident(session_id: str) -> dict:
    """Kanaloption 1: Video-Ident -- schnell, aber Terminslot noetig."""
    return {
        "session_id": session_id,
        "channel": "video_ident",
        "eta_minutes": 15,
        "requires_live_slot": True,
        "provider": "IDnow / WebID (Buy, externer Video-Ident-Dienstleister)",
    }


@mcp.tool()
def request_postident(session_id: str) -> dict:
    """Kanaloption 2: PostIdent -- kein Termin, aber mehrtaegige Laufzeit."""
    return {
        "session_id": session_id,
        "channel": "postident",
        "eta_days": 3,
        "requires_live_slot": False,
        "provider": "Deutsche Post PostIdent (Buy)",
    }


@mcp.tool()
def request_eid(session_id: str) -> dict:
    """
    Kanaloption 3: Online-Ausweisfunktion (eID) -- schnellste, vollautomatisierbare
    Option, setzt aber Personalausweis mit aktivierter eID-PIN und
    AusweisApp2 (oder eine SDK-Integration wie z. B. Governikus/OpenEID)
    beim Kunden voraus. Fuer Agentic Commerce der wichtigste Kanal, weil er
    ohne Terminslot und ohne Medienbruch vollstaendig automatisierbar ist.
    """
    return {
        "session_id": session_id,
        "channel": "eid",
        "eta_minutes": 5,
        "requires_live_slot": False,
        "provider": "AusweisApp2 / eID-Server (Buy: Bundesdruckerei-Infrastruktur)",
    }


@mcp.tool()
def submit_identity(session_id: str, channel: str, verification_ref: str) -> dict:
    """Schliesst die Identitaetspruefung fuer den gewaehlten Kanal ab."""
    if session_id not in SESSIONS:
        return {"session_id": session_id, "identity_verified": False, "error": "unknown_session"}
    SESSIONS[session_id]["identity_verified"] = True
    SESSIONS[session_id]["ident_channel"] = channel
    return {"session_id": session_id, "identity_verified": True, "channel": channel}


@mcp.tool()
def submit_tax_info(session_id: str, tax_id: str, tax_residency: str = "DE") -> dict:
    """Nimmt steuerliche Angaben entgegen."""
    SESSIONS.setdefault(session_id, {})["tax_info_complete"] = True
    return {"session_id": session_id, "tax_info_complete": True}


@mcp.tool()
def run_suitability_check(
    session_id: str,
    experience_years: int,
    product_knowledge: str,
    investment_horizon_years: int,
    loss_tolerance: str,
) -> dict:
    """
    Fuehrt eine vereinfachte MiFID-II-Geeignetheitspruefung durch.

    product_knowledge: "gering" | "mittel" | "hoch"
    loss_tolerance: "gering" | "mittel" | "hoch"
    """
    knowledge_points = {"gering": 0, "mittel": 1, "hoch": 2}.get(product_knowledge, 0)
    loss_points = {"gering": 0, "mittel": 1, "hoch": 2}.get(loss_tolerance, 0)
    experience_points = min(experience_years, 10) / 5  # 0..2
    horizon_points = min(investment_horizon_years, 10) / 5  # 0..2

    total = knowledge_points + loss_points + experience_points + horizon_points  # 0..8
    if total >= 5:
        risk_score = "niedrig"
    elif total >= 2.5:
        risk_score = "mittel"
    else:
        risk_score = "hoch"

    needs_human_review = risk_score == "hoch" or product_knowledge == "gering"

    result = {
        "session_id": session_id,
        "risk_score": risk_score,
        "score_value": round(total, 2),
        "needs_human_review": needs_human_review,
    }
    SESSIONS.setdefault(session_id, {})["suitability"] = result
    return result


@mcp.tool()
def request_human_review(session_id: str, reason: str) -> dict:
    """Triggert das HITL-Gate. Der Mensch entscheidet ausserhalb dieses Tools."""
    SESSIONS.setdefault(session_id, {})["human_review_requested"] = reason
    return {"session_id": session_id, "review_requested": True, "reason": reason}


@mcp.tool()
def sign_contract(session_id: str, approved: bool) -> dict:
    """Schliesst den Vertrag ab (simulierte qualifizierte E-Signatur)."""
    if not approved:
        return {"session_id": session_id, "contract_signed": False}
    depot_id = "DEPOT-" + str(uuid.uuid4())[:10].upper()
    SESSIONS.setdefault(session_id, {})["depot_id"] = depot_id
    return {"session_id": session_id, "contract_signed": True, "depot_id": depot_id}


if __name__ == "__main__":
    #mcp.run(transport="stdio")
    mcp.run()