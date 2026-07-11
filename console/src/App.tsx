import { useEffect, useMemo, useRef, useState } from "react";
import { AGENTS, EvidenceLeaf, Hypothesis, ICEvent, Incident } from "./types";
import MetricsPanel from "./MetricsPanel";

type LaneLine = { t: number; text: string; kind: string };
type Ambiguity = { margin: number; tau: number; reason?: string; resolvable: boolean; note?: string } | null;
type ProbeSel = { probe_id: string; info_gain: number; cost_ms: number; description: string } | null;
type ProbeRes = { probe_id: string; summary: string; hash: string; source_uri: string } | null;
type Sealed = { merkle_root: string; signature: string; leaf_count: number } | null;
type Verdict = { root_cause_id: string; summary: string; posterior: number; rollback_recommended: boolean; probes_run: string[] } | null;

const AGENT_LABEL: Record<string, string> = {
  change_agent: "Change Agent",
  telemetry_agent: "Telemetry Agent",
  history_agent: "History Agent",
};

export default function App() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [sel, setSel] = useState<string>("INC-4471");
  const [probesOn, setProbesOn] = useState(true);
  const [replay, setReplay] = useState(true);
  const [tab, setTab] = useState<"console" | "metrics">("console");
  const [running, setRunning] = useState(false);

  const [lanes, setLanes] = useState<Record<string, LaneLine[]>>({});
  const [hyps, setHyps] = useState<Record<string, Hypothesis>>({});
  const [order, setOrder] = useState<string[]>([]);
  const [ambiguity, setAmbiguity] = useState<Ambiguity>(null);
  const [probeSel, setProbeSel] = useState<ProbeSel>(null);
  const [probeRes, setProbeRes] = useState<ProbeRes>(null);
  const [exhausted, setExhausted] = useState<any>(null);
  const [leaves, setLeaves] = useState<EvidenceLeaf[]>([]);
  const [sealed, setSealed] = useState<Sealed>(null);
  const [verdict, setVerdict] = useState<Verdict>(null);
  const [gate, setGate] = useState<any>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const startRef = useRef<number>(0);

  useEffect(() => {
    fetch("/incidents").then((r) => r.json()).then(setIncidents).catch(() => {});
  }, []);

  const incident = incidents.find((i) => i.incident_id === sel);

  function reset() {
    setLanes({}); setHyps({}); setOrder([]); setAmbiguity(null);
    setProbeSel(null); setProbeRes(null); setExhausted(null);
    setLeaves([]); setSealed(null); setVerdict(null); setGate(null);
  }

  function addLeaf(uri: string, hash?: string, probe?: boolean) {
    setLeaves((prev) => {
      const existing = prev.find((l) => l.uri === uri);
      if (existing) {
        if (hash && !existing.hash) return prev.map((l) => (l.uri === uri ? { ...l, hash, probe } : l));
        return prev;
      }
      return [...prev, { uri, hash: hash || "", probe: !!probe }];
    });
  }

  function handle(e: ICEvent) {
    const t = Math.round(performance.now() - startRef.current);
    switch (e.type) {
      case "agent_started":
        setLanes((p) => ({ ...p, [e.agent_id]: p[e.agent_id] || [] }));
        break;
      case "agent_reasoned": {
        const line: LaneLine = {
          t,
          kind: "reason",
          text: `${e.hyp_id}  ${e.delta >= 0 ? "+" : ""}${e.delta}  ${e.rationale}`,
        };
        setLanes((p) => ({ ...p, [e.agent_id]: [...(p[e.agent_id] || []), line] }));
        (e.evidence_refs || []).forEach((r: string) => addLeaf(r));
        break;
      }
      case "hypothesis_proposed":
        setHyps((p) => ({
          ...p,
          [e.id]: { id: e.id, claim: e.claim, agent_id: e.agent_id, posterior: e.posterior, eliminated: false, evidence_refs: e.evidence_refs || [] },
        }));
        setOrder((o) => (o.includes(e.id) ? o : [...o, e.id]));
        (e.evidence_refs || []).forEach((r: string) => addLeaf(r));
        break;
      case "posterior_updated":
        setHyps((p) => ({ ...p, [e.id]: { ...p[e.id], posterior: e.posterior } }));
        break;
      case "hypothesis_eliminated":
        setHyps((p) => ({ ...p, [e.id]: { ...p[e.id], eliminated: true, eliminated_reason: e.reason } }));
        break;
      case "ambiguity_detected":
        setAmbiguity({ margin: e.margin, tau: e.tau, reason: e.reason, resolvable: e.resolvable, note: e.note });
        break;
      case "probe_selected":
        setProbeSel({ probe_id: e.probe_id, info_gain: e.info_gain, cost_ms: e.cost_ms, description: e.description });
        break;
      case "probe_result":
        setProbeRes({ probe_id: e.probe_id, summary: e.summary, hash: e.hash, source_uri: e.source_uri });
        addLeaf(e.source_uri, e.hash, true);
        break;
      case "exhausted":
        setExhausted(e);
        break;
      case "gate_pending":
        setGate(e);
        break;
      case "verdict":
        setVerdict({ root_cause_id: e.root_cause_id, summary: e.summary, posterior: e.posterior, rollback_recommended: e.rollback_recommended, probes_run: e.probes_run });
        break;
      case "chain_sealed":
        setSealed({ merkle_root: e.merkle_root, signature: e.signature, leaf_count: e.leaf_count });
        break;
      case "done":
        setRunning(false);
        break;
    }
  }

  function start() {
    if (wsRef.current) wsRef.current.close();
    reset();
    setRunning(true);
    startRef.current = performance.now();
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    wsRef.current = ws;
    ws.onopen = () => ws.send(JSON.stringify({ incident_id: sel, probes_enabled: probesOn, replay }));
    ws.onmessage = (m) => handle(JSON.parse(m.data));
    ws.onclose = () => setRunning(false);
  }

  const ranked = useMemo(
    () => order.map((id) => hyps[id]).filter(Boolean).sort((a, b) => Number(a.eliminated) - Number(b.eliminated) || b.posterior - a.posterior),
    [order, hyps]
  );

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="dot" /> INCIDENT COMMANDER
          <span className="tagline">active differential diagnosis</span>
        </div>
        <div className="tabs">
          <button className={tab === "console" ? "on" : ""} onClick={() => setTab("console")}>Console</button>
          <button className={tab === "metrics" ? "on" : ""} onClick={() => setTab("metrics")}>Ablation &amp; Calibration</button>
        </div>
      </header>

      {tab === "metrics" ? (
        <MetricsPanel />
      ) : (
        <>
          <div className="controls">
            <select value={sel} onChange={(e) => setSel(e.target.value)} disabled={running}>
              {incidents.map((i) => (
                <option key={i.incident_id} value={i.incident_id}>
                  {i.incident_id} · {i.slice} · {i.title.slice(0, 46)}…
                </option>
              ))}
            </select>
            <label className="chk"><input type="checkbox" checked={probesOn} onChange={(e) => setProbesOn(e.target.checked)} disabled={running} /> probes</label>
            <label className="chk"><input type="checkbox" checked={replay} onChange={(e) => setReplay(e.target.checked)} disabled={running} /> replay</label>
            <button className="run" onClick={start} disabled={running || !incident}>{running ? "investigating…" : "▶ Investigate"}</button>
            {incident && <span className="truth">ground truth · {incident.ground_truth.root_cause_id} · rollback {String(incident.ground_truth.rollback_correct)}</span>}
          </div>

          <div className="grid">
            {/* PANE 1 — agent lanes */}
            <section className="pane lanes">
              <h2>Agent lanes</h2>
              <div className="laneRow">
                {AGENTS.map((a) => (
                  <div key={a} className="lane">
                    <div className="laneHead">{AGENT_LABEL[a]}</div>
                    <div className="laneBody">
                      {(lanes[a] || []).map((l, i) => (
                        <div key={i} className="laneLine">
                          <span className="ms">{l.t}ms</span> {l.text}
                        </div>
                      ))}
                      {!(lanes[a] || []).length && <div className="idle">idle</div>}
                    </div>
                  </div>
                ))}
              </div>
            </section>

            {/* PANE 3 — probe ticker (given prominence: it's the wow) */}
            <section className="pane ticker">
              <h2>Probe selection</h2>
              {!ambiguity && <div className="idle">awaiting adjudication…</div>}
              {ambiguity && (
                <div className={`ambiguity ${ambiguity.resolvable ? "" : "blocked"}`}>
                  <div className="banner">
                    Hypotheses within <b>{ambiguity.margin.toFixed(3)}</b> (τ={ambiguity.tau})
                    {ambiguity.resolvable ? " — selecting discriminating probe…" : " — probes OFF, cannot resolve"}
                  </div>
                  {ambiguity.reason && <div className="why">{ambiguity.reason}</div>}
                  {ambiguity.note && <div className="why">{ambiguity.note}</div>}
                </div>
              )}
              {probeSel && (
                <div className="probeCard">
                  <div className="pid">{probeSel.probe_id}</div>
                  <div className="pdesc">{probeSel.description}</div>
                  <div className="pmeta">
                    <span className="gain">info-gain {probeSel.info_gain}</span>
                    <span>{probeSel.cost_ms} ms</span>
                  </div>
                </div>
              )}
              {probeRes && (
                <div className="probeRes">
                  <div className="prLabel">probe returned</div>
                  <div className="prSummary">{probeRes.summary}</div>
                  <div className="prHash">sha256 {probeRes.hash}…</div>
                </div>
              )}
              {exhausted && (
                <div className="exhausted">
                  budget exhausted — surviving {exhausted.surviving?.join(", ")}; would run next: <b>{exhausted.would_run_next}</b>
                </div>
              )}
            </section>

            {/* PANE 2 — hypothesis board */}
            <section className="pane board">
              <h2>Hypothesis board</h2>
              {ranked.map((h) => (
                <div key={h.id} className={`hyp ${h.eliminated ? "dead" : ""} ${verdict?.root_cause_id === h.id && !h.eliminated ? "winner" : ""}`}>
                  <div className="hypTop">
                    <span className="hid">{h.id}</span>
                    <span className="owner">{AGENT_LABEL[h.agent_id] || h.agent_id}</span>
                    <span className="pct">{(h.posterior * 100).toFixed(0)}%</span>
                  </div>
                  <div className="claim">{h.claim}</div>
                  <div className="bar"><div className="fill" style={{ width: `${h.posterior * 100}%` }} /></div>
                  {h.eliminated && <div className="elim">✕ {h.eliminated_reason}</div>}
                  {!!h.evidence_refs.length && <div className="refs">{h.evidence_refs.map((r) => r.split("//").pop()).join(" · ")}</div>}
                </div>
              ))}
              {verdict && (
                <div className="verdict">
                  <div className="vhead">VERDICT — {verdict.root_cause_id} @ {(verdict.posterior * 100).toFixed(0)}%</div>
                  <div className="vsum">{verdict.summary}</div>
                  <div className={`rollback ${verdict.rollback_recommended ? "yes" : "no"}`}>
                    rollback {verdict.rollback_recommended ? "RECOMMENDED" : "not recommended"}
                  </div>
                  {gate && <div className="gated">⛔ {gate.action} gated — awaiting explicit approval (never auto-fired)</div>}
                </div>
              )}
            </section>

            {/* PANE 4 — evidence chain */}
            <section className="pane chain">
              <h2>Evidence chain</h2>
              <div className="leaves">
                {leaves.map((l, i) => (
                  <div key={i} className={`leaf ${l.probe ? "probe" : ""}`}>
                    <span className="lhash">{l.hash ? l.hash.slice(0, 12) : "prior"}</span>
                    <span className="luri">{l.uri.split("//").pop()}</span>
                  </div>
                ))}
                {!leaves.length && <div className="idle">no evidence cited yet</div>}
              </div>
              <div className={`seal ${sealed ? "sealed" : ""}`}>
                {sealed ? (
                  <>
                    <div className="sealStamp">✓ SEALED · {sealed.leaf_count} leaves</div>
                    <div className="root">merkle {sealed.merkle_root.slice(0, 24)}…</div>
                    <div className="sig">ed25519 {sealed.signature}</div>
                  </>
                ) : (
                  <div className="idle">chain open — computes root on verdict</div>
                )}
              </div>
            </section>
          </div>
        </>
      )}
      <footer>local corpus · local server · offline verifier — <code>python verify.py postmortem.json</code></footer>
    </div>
  );
}
