"""
CLI-Runner fuer die echte-MCP-Version. Fuer die Web-UI siehe web/app.py.

Ausfuehren: python main_cli.py
"""

import asyncio

from agent_client import run_scenario


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
    }
    print("Kunde: 'Richte mir einen nachhaltigen ETF-Sparplan ueber 200 EUR/Monat ein.'\n")
    await run_scenario(customer_profile, print_event, ask_human)


if __name__ == "__main__":
    asyncio.run(main())
