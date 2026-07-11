"""FastAPI + WebSocket server. Streams orchestrator events to the console.

Run:  uvicorn server.app:app --reload --port 8000

The event stream can be served live (runs the orchestrator) or from a recorded replay
(`replays/INC-XXXX.json`) so a live LLM hiccup can never break a stage demo. Events are
paced with deliberate beats on the ambiguity/probe moments — that is the wow.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ic.bundle import load_bundle, load_corpus
from ic.harness import main as run_harness
from ic.orchestrator import run_investigation

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
REPLAY_DIR = ROOT / "replays"

app = FastAPI(title="Incident Commander")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Deliberate pacing (seconds) per event type — the probe beat is the theatre.
PACE = {
    "agent_started": 0.25, "agent_reasoned": 0.35, "hypothesis_proposed": 0.5,
    "ambiguity_detected": 1.3, "probe_selected": 1.1, "probe_result": 0.9,
    "posterior_updated": 0.5, "hypothesis_eliminated": 0.8, "exhausted": 0.8,
    "gate_pending": 0.7, "verdict": 0.6, "chain_sealed": 0.6,
}


@app.get("/incidents")
def incidents():
    out = []
    for b in load_corpus(CORPUS):
        out.append({"incident_id": b.incident_id, "slice": b.slice, "title": b.title,
                    "alert": b.alert, "ground_truth": b.ground_truth,
                    "has_replay": (REPLAY_DIR / f"{b.incident_id}.json").exists()})
    return out


@app.get("/harness")
def harness():
    path = ROOT / "harness_results.json"
    if not path.exists():
        run_harness(str(CORPUS), str(path))
    return JSONResponse(json.loads(path.read_text()))


@app.post("/investigate")
def investigate(body: dict):
    """Non-streaming: run and return the full result (handy for tests)."""
    b = load_bundle(CORPUS / f"{body['incident_id']}.json")
    res = run_investigation(b, probes_enabled=body.get("probes_enabled", True))
    return {"verdict": res.verdict.model_dump(), "events": res.events,
            "probes_run": res.probes_run}


def _events_for(incident_id: str, probes_enabled: bool, replay: bool) -> list[dict]:
    if replay:
        p = REPLAY_DIR / f"{incident_id}.json"
        if p.exists():
            return json.loads(p.read_text())
    b = load_bundle(CORPUS / f"{incident_id}.json")
    return run_investigation(b, probes_enabled=probes_enabled).events


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    try:
        params = await sock.receive_json()
        events = _events_for(params["incident_id"],
                             params.get("probes_enabled", True),
                             params.get("replay", False))
        speed = float(params.get("speed", 1.0))
        for e in events:
            await sock.send_json(e)
            await asyncio.sleep(PACE.get(e["type"], 0.4) / max(speed, 0.1))
        await sock.send_json({"type": "done"})
    except WebSocketDisconnect:
        return


def record_replays() -> None:
    REPLAY_DIR.mkdir(exist_ok=True)
    for b in load_corpus(CORPUS):
        res = run_investigation(b, probes_enabled=True, use_llm=False)  # stable replays
        (REPLAY_DIR / f"{b.incident_id}.json").write_text(json.dumps(res.events, indent=2))
        print(f"recorded {b.incident_id}: {len(res.events)} events")


if __name__ == "__main__":
    record_replays()
