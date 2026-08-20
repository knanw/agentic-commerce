"""
CLI-Runner fuer die LangGraph-Version.

Ausfuehren: python main_langgraph.py
Mit echtem LLM fuer die Praeferenz-Extraktion: ANTHROPIC_API_KEY=... python main_langgraph.py
"""

import asyncio

from agent_langgraph import run_scenario_langgraph


def print_event(event: dict):
    detail = f" -> {event['detail']}" if event["detail"] else ""
    print(f"  [{event['agent']}] {event['step']}{detail}")


async def ask_human(session_id: str, suitability: dict) -> bool:
    print(f"\n  >>> MENSCHLICHE FREIGABE ERFORDERLICH (Session {session_id}) <<<")
    print(f"      Risiko-Score: {suitability['risk_score']} (Wert: {suitability['score_value']})")
    answer = await asyncio.to_thread(input, "      Freigeben? [j/n]: ")
    return answer.strip().lower() == "j"


async def main():
    customer_profile = {
        "raw_request": "Ich möchte nachhaltig 200 Euro im Monat sparen.",
        "customer_data": {
            "vorname": "Max", "nachname": "Mustermann", "geburtsdatum": "1990-05-14",
            "geburtsort": "Berlin", "staatsangehoerigkeit": "deutsch",
            "adresse": "Musterstraße 1, 10115 Berlin", "email": "max.mustermann@example.com",
            "telefon": "+49 151 00000000", "steuer_id": "12345678901", "steuerlich_ansaessig_in": "DE",
        },
        "tax_id": "12345678901",
        "ident_channel": "video_ident",
        "risk_profile": {
            "experience_years": 2,
            "product_knowledge": "gering",
            "investment_horizon_years": 8,
            "loss_tolerance": "mittel",
        },
    }
    print(f"Kunde: '{customer_profile['raw_request']}'\n")
    summary = await run_scenario_langgraph(customer_profile, print_event, ask_human)
    print("\nErgebnis:", summary)


if __name__ == "__main__":
    asyncio.run(main())
