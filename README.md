# Agentenbasierte Depoteröffnung — Prototyp

Lauffähiger Prototyp zum technischen Zielkonzept aus der Case Study: ein
Kundenagent (simuliert) eröffnet über drei echte MCP-Server (Discovery,
Onboarding, Trading) und eine LangGraph-State-Machine ein Depot samt
ETF-Sparplan — inklusive Human-in-the-loop-Freigabe bei auffälligem
Risiko-Score.

## Kurzüberblick: was hier passiert

Ein "Kunde" formuliert eine Anfrage ("Ich möchte nachhaltig 200 €/Monat
sparen") und füllt ein Formular mit Personendaten aus. Ein simulierter
Kundenagent (LangGraph-State-Machine) übernimmt danach den ganzen Flow:
Kundendaten prüfen → Produkt vergleichen → Depot eröffnen (inkl. Ident,
Steuerangaben, Geeignetheitsprüfung, ggf. menschliche Freigabe, Vertrag) →
Sparplan einrichten. Jeder Schritt läuft als echter MCP-Tool-Call gegen
einen von drei separaten MCP-Servern. Die Web-UI zeigt jeden Schritt live.

Ausführliche Erklärung, welcher Agent wann welchen Server aufruft: siehe
Abschnitt "Ablauf im Detail" unten.

## Architektur

```
Browser (Vanilla-UI oder Vue-UI)
        │  WebSocket (/ws-langgraph)
        ▼
FastAPI-Backend (app.py)
        │  LangGraph-State-Machine (agent_langgraph.py)
        │  MCP-Protokoll (stdio lokal / SSE / SSE(Docker))
        ▼
┌───────────────┬───────────────┬───────────────┐
│ discovery_     │ onboarding_    │ trading_       │
│ server.py      │ server.py      │ server.py      │
│ (Produkte,     │ (Ident, Steuer,│ (Sparplan,     │
│  Anbieter-ID)  │  Geeignetheit, │  Order)        │
│                │  HITL, Vertrag)│                │
└───────────────┴───────────────┴───────────────┘
```

## Überblick: Was das System bietet

| Komponente                                                      | Zweck                                                                                                                                                               | Wie man darauf zugreift                                                                                                                 |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| **MCP‑Server** (Discovery, Onboarding, Trading)                 | Stellen fachspezifische **Tools** bereit (z. B. `create_depot_account`, `compare_products`, `execute_trade`).                                                       | Über das **Model Context Protocol** (MCP) – entweder stdio‑Transport (lokal) oder SSE‑Transport (HTTP).                                 |
| **LangGraph‑Orchestrator** (`agent_langraph.py`)                | Steuerlicher End‑to‑End‑Workflow (KYC → Onboarding → Handel). Wird über einen WebSocket‑Endpoint (`/ws-langgraph`) angestoßen.                                      | Akzeptiert ein JSON‑Profil (Kundendaten, Anfrage usw.) und liefert per WebSocket Events, HITL‑Abfragen und eine finale Zusammenfassung. |
| **Web‑Backend** (`app.py`)                                      | Stellt HTTP‑Endpunkte (`/`, `/vue`, `/tools*`) und die WebSocket‑Endpunkte bereit. Zusätzlich ein HTTP‑Endpoint `/tools` zum Auflisten aller verfügbaren MCP‑Tools. | Aufruf über `http://<host>:<port>/…` (Standard: `localhost:8000`).                                                                      |
| **Frontend** (`static/index.html` oder `static-vue/index.html`) | Beispiel‑UI, die das WebSocket‑Protokoll demonstriert.                                                                                                              | Öffnen Sie `http://localhost:8000/` (oder `/vue` für das Vue‑Frontend).                                                                 |

> **Kernidee**: Der LangGraph ist lediglich ein **Koordinator** – die eigentliche Fachlogik lebt in den MCP‑Servern. Andere können entweder den kompletten Orchestrator nutzen, einzelne Tools direkt aufrufen oder eigene Schritte in den Graph einbauen.

---

## Ablauf im Detail (was passiert bei einem Klick auf "Starten")

1. **Browser → Backend**: Formular (Name, Adresse, Steuer-ID, Ident-Kanal,
   Wertpapierkenntnisse, Freitext-Wunsch) wird per WebSocket an
   `/ws-langgraph` gesendet.
2. **Kundenagent** parst den Freitext in strukturierte Präferenzen (ESG,
   Risikoklasse, Kostensensitivität, Sparbetrag).
3. **Depot-Agent** prüft über `onboarding_server.check_required_fields`, ob
   alle Pflichtangaben da sind. Fehlt etwas → Abbruch mit Meldung, kein
   MCP-Call an die anderen Server.
4. **Research-Agent** ruft `discovery_server.verify_provider_identity` und
   `discovery_server.compare_products` auf, wählt das beste Produkt.
5. **Depot-Agent** ruft nacheinander `onboarding_server`:
   `initiate_onboarding` → `request_video_ident`/`request_eid`/
   `request_postident` (je nach gewähltem Kanal) → `submit_identity` →
   `submit_tax_info` → `run_suitability_check`.
6. Ist der Risiko-Score auffällig: **HITL-Gate** — der Graph pausiert
   (`request_human_review` + `interrupt()`), die UI zeigt einen
   Freigeben/Ablehnen-Button, der Klick geht zurück per WebSocket, der Graph
   läuft mit `Command(resume=...)` an exakt dieser Stelle weiter.
7. **Depot-Agent** ruft `onboarding_server.sign_contract`.
8. **Anleger-Agent** ruft `trading_server.create_savings_plan`.
9. **Kundenagent** fasst zusammen, Backend schickt `done` an den Browser.

## Verfügbare Tools ermitteln

Jeder MCP‑Server stellt die Methode `list_tools()` bereit. Über das Web‑Backend gibt es bereits Endpunkte, um sie zu prüfen:

| Endpunkt                   | Zweck                                                             | Beispielantwort                                                  |
| -------------------------- | ----------------------------------------------------------------- | ---------------------------------------------------------------- |
| `GET /tools/{server_name>` | Liste der Tools eines bestimmten Servers                          | `{"server":"onboarding","tools":[..."create_depot_account"...]}` |
| `GET /tools`               | Liste der Tools **aller** Server (Discovery, Onboarding, Trading) | `{ "discovery": […], "onboarding": […], "trading": [...] }`      |
| `GET /openapi.json`        | Rohes OpenAPI‑Schema (für Swagger/ReDoc)                          | –                                                                |
| `GET /docs`                | Swagger UI (interaktive API‑Dokumentation)                        | –                                                                |
| `GET /redoc`               | ReDoc‑Anzeige                                                     | –                                                                |

---

## Ausführen (lokal, stdio — kein Docker nötig)

```bash
python3 -m venv .venv && source .venv/bin/activate   # oder: uv sync
pip install -r requirements.txt

python main_cli.py          # CLI, einfache Version (v1)
python main_langgraph.py    # CLI, LangGraph-Version (v2)

python -m uvicorn app:app --reload
# Vanilla-UI:  http://127.0.0.1:8000/
# Vue-UI:      http://127.0.0.1:8000/vue
```

Mit echtem LLM für die Präferenz-Extraktion: `.env`-Datei mit
`OPENAI_API_KEY=sk-...` anlegen (wird per `python-dotenv` geladen). Ohne Key
läuft automatisch die Keyword-Heuristik.

## Ausführen mit Docker (SSE-Transport, cloud-näher)

```bash
docker compose build --no-cache
docker compuse up

#oder
docker compose up --build
```

Startet vier Container: `discovery` (Port 8001), `onboarding` (Port 8002),
`trading` (Port 8003) — jeweils als `fastmcp run ... --transport sse`
gestarteter MCP-Server — und `web` (Port 8000), das sich per SSE statt
stdio mit den dreien verbindet (`agent_client.MCPServerConnection` wählt den
Transport automatisch: `sse_url` gesetzt → SSE, sonst stdio-Subprozess).
Optional: `OPENAI_API_KEY=sk-... docker compose up --build`.

## Struktur

| Datei                               | Inhalt                                                                        |
| ----------------------------------- | ----------------------------------------------------------------------------- |
| `mcp_servers/discovery_server.py`   | MCP-Server: Produktkatalog, Anbieter-Identität, Produktvergleich              |
| `mcp_servers/onboarding_server.py`  | MCP-Server: Pflichtfeld-Check, Ident-Kanäle, KYC, Geeignetheit, HITL, Vertrag |
| `mcp_servers/trading_server.py`     | MCP-Server: Sparplan/Order                                                    |
| `agent_client.py`                   | v1-Agenten + `MCPServerConnection` (stdio/SSE)                                |
| `agent_langgraph.py`                | v2: LangGraph-State-Machine + LLM-NLU-Knoten + Datenvalidierung               |
| `main_cli.py` / `main_langgraph.py` | Terminal-Runner für v1/v2                                                     |
| `app.py`                            | FastAPI-Backend, `/ws` (v1) und `/ws-langgraph` (v2)                          |
| `static/index.html`                 | Vanilla-JS-UI mit Formular                                                    |
| `static-vue/index.html`             | Vue-3-UI mit Formular                                                         |
| `docker-compose.yml`, `Dockerfile`  | Container-Setup (SSE-Transport)                                               |

## OpenAPI Specification

- http://localhost:8000/docs Swagger‑UI (interaktive API‑Konsole)
- http://localhost:8000/redoc ReDoc‑Anzeige (schöneres Layout, ebenfalls interaktiv)
- http://localhost:8000/openapi.json Rohes OpenAPI‑JSON‑Schema (kann von Tools wie Swagger Editor, Postman etc. importiert werden)
