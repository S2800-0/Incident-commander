import { useEffect, useMemo, useRef, useState } from "react";
import { AGENTS, EvidenceLeaf, Hypothesis, ICEvent, Incident } from "./types";
import MetricsPanel from "./MetricsPanel";

type LaneLine = { t: number; text: string; kind: string };
type Ambiguity = { margin: number; tau: number; reason?: string; resolvable: boolean; note?: string } | null;
type ProbeSel = { probe_id: string; info_gain: number; cost_ms: number; description: string } | null;
type ProbeRes = { probe_id: string; summary: string; hash: string; source_uri: string } | null;
type Sealed = { merkle_root: string; signature: string; leaf_count: number } | null;
type Verdict = { root_cause_id: string; summary: string; posterior: number; rollback_recommended: boolean; probes_run: string[] } | null;
type VoiAction = { action_id: string; kind: string; eig: number; cost: number; risk: number; voi_score: number; executable: boolean; description?: string };
type VoiState = { step: number; actions: VoiAction[] } | null;
type Stagnation = { best_observation: string | null; best_observation_eig: number; intervention_eig: number | null; intervention_available?: boolean } | null;
type GatePending = { action: string; intervention_id?: string; description?: string; safety_envelope?: any; note?: string } | null;
type InterventionResult = { intervention_id: string; summary: string; hash: string } | null;

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
  const [voi, setVoi] = useState<VoiState>(null);
  const [stagnation, setStagnation] = useState<Stagnation>(null);
  const [interventionWouldFire, setInterventionWouldFire] = useState<any>(null);
  const [provenance, setProvenance] = useState<string | null>(null);
  const [interventionGate, setInterventionGate] = useState<GatePending>(null);
  const [interventionApproved, setInterventionApproved] = useState(false);
  const [interventionResult, setInterventionResult] = useState<InterventionResult>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const startRef = useRef<number>(0);
  // Events arriving while the intervention gate is open are buffered here so the
  // console visibly pauses on "human approval required" — the click drains the queue.
  const gatePausedRef = useRef<boolean>(false);
  const queuedRef = useRef<ICEvent[]>([]);

  useEffect(() => {
    fetch("/incidents").then((r) => r.json()).then(setIncidents).catch(() => {});
  }, []);

  const incident = incidents.find((i) => i.incident_id === sel);

  function reset() {
    setLanes({}); setHyps({}); setOrder([]); setAmbiguity(null);
    setProbeSel(null); setProbeRes(null); setExhausted(null);
    setLeaves([]); setSealed(null); setVerdict(null); setGate(null);
    setVoi(null); setStagnation(null); setInterventionWouldFire(null); setProvenance(null);
    setInterventionGate(null); setInterventionApproved(false); setInterventionResult(null);
    gatePausedRef.current = false;
    queuedRef.current = [];
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
      case "voi_scored":
        setVoi({ step: e.step, actions: e.actions });
        break;
      case "voi_stagnation_detected":
        setStagnation({ best_observation: e.best_observation, best_observation_eig: e.best_observation_eig, intervention_eig: e.intervention_eig, intervention_available: e.intervention_available });
        break;
      case "intervention_would_fire":
        setInterventionWouldFire(e);
        break;
      case "provenance_labeled":
        setProvenance(e.provenance);
        break;
      case "gate_pending":
        if (e.action === "intervention") {
          // PAUSE — the human must click Approve before subsequent events render.
          setInterventionGate({ action: e.action, intervention_id: e.intervention_id, description: e.description, safety_envelope: e.safety_envelope, note: e.note });
          gatePausedRef.current = true;
        } else {
          setGate(e); // rollback gate — informational, doesn't pause
        }
        break;
      case "intervention_executed":
        setInterventionResult({ intervention_id: e.intervention_id, summary: e.summary, hash: e.hash });
        addLeaf(e.source_uri, e.hash, true);
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

  // Buffer events that arrive while the intervention gate is open — they render
  // only after the user clicks Approve.
  function dispatch(e: ICEvent) {
    if (gatePausedRef.current && e.type !== "gate_pending") {
      queuedRef.current.push(e);
      return;
    }
    handle(e);
  }

  function approveIntervention() {
    gatePausedRef.current = false;
    setInterventionApproved(true);
    const queued = queuedRef.current;
    queuedRef.current = [];
    // Drain with light pacing so the collapse still reads as a sequence, not a jump.
    queued.forEach((e, i) => setTimeout(() => handle(e), i * 320));
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
    ws.onmessage = (m) => dispatch(JSON.parse(m.data));
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
              {interventionGate && !interventionApproved && (
                <div className="interventionGate">
                  <div className="ivGateHead">HUMAN APPROVAL REQUIRED · INTERVENTION GATED</div>
                  <div className="ivGateDesc">{interventionGate.description}</div>
                  {interventionGate.safety_envelope && (
                    <div className="ivEnv">
                      safety envelope · max {interventionGate.safety_envelope.max_traffic_pct}% traffic ·
                      bounded {interventionGate.safety_envelope.max_duration_s}s ·
                      auto-revert {String(interventionGate.safety_envelope.auto_revert)}
                    </div>
                  )}
                  <div className="ivReason">
                    Observation VoI has stagnated — passive checks cannot resolve this. A bounded
                    causal test is the highest-value next action. Nothing happens until you approve.
                  </div>
                  <button className="approveBtn" onClick={approveIntervention}>
                    ✓ Approve intervention & execute
                  </button>
                </div>
              )}
              {interventionApproved && interventionResult && (
                <div className="interventionResult">
                  <div className="ivResultHead">✓ INTERVENTION EXECUTED · {interventionResult.intervention_id}</div>
                  <div className="ivResultSummary">{interventionResult.summary}</div>
                  <div className="ivResultHash">sha256 {interventionResult.hash}…</div>
                </div>
              )}
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
                  {provenance && (
                    <div className={`provenance ${interventionWouldFire ? "wouldbe" : ""}`}>
                      Provenance: {interventionWouldFire ? "interventional (would-be)" : provenance}
                    </div>
                  )}
                  {gate && <div className="gated">⛔ {gate.action} gated — awaiting explicit approval (never auto-fired)</div>}
                </div>
              )}
            </section>

            {/* PANE 5 — VoI Stagnation Panel (framing overlay: explains WHY the chosen
                action was chosen, incl. why the intervention would fire if executable) */}
            <section className="pane voi">
              <h2>VoI · Value of the Next Action</h2>
              {!voi && <div className="idle">awaiting action scoring…</div>}
              {voi && (() => {
                const obs = voi.actions.filter((a) => a.kind === "observation");
                const interv = voi.actions.find((a) => a.kind === "intervention");
                const maxEig = Math.max(1, ...voi.actions.map((a) => a.eig));
                const argmax = voi.actions.find((a) => a.executable);
                return (
                  <>
                    {stagnation && (
                      <div className="stagBanner">
                        Observation VoI collapsed → probing is the last productive step in this loop.
                      </div>
                    )}
                    {interventionWouldFire && (
                      <div className="stagBanner fire">
                        Observation exhausted. Intervention would be selected — traffic_shift at 5%, 60s bound. Phase 2 executes; Phase 1 explains.
                      </div>
                    )}
                    {obs.map((a) => (
                      <div key={a.action_id} className={`voiRow ${stagnation ? "stagnated" : ""}`}>
                        <span className="voiLabel">{a.action_id}</span>
                        <div className="voiBar"><div className="voiFill obs" style={{ width: `${Math.max(2, (a.eig / maxEig) * 100)}%` }} /></div>
                        <span className="voiNum">EIG {a.eig.toFixed(2)}</span>
                      </div>
                    ))}
                    {interv && (
                      <div className="voiRow">
                        <span className="voiLabel iv" title="Traffic-shift primitive (5% canary, 60s bound, auto-revert). Specified in our research design (Section 5). Not executed in prototype — see Phase 2.">
                          {interv.action_id.replace("_placeholder", "")} (not executable) ⓘ
                        </span>
                        <div className="voiBar"><div className="voiFill iv" style={{ width: `${Math.max(2, (interv.eig / maxEig) * 100)}%` }} /></div>
                        <span className="voiNum">EIG {interv.eig.toFixed(2)}</span>
                      </div>
                    )}
                    <div className="voiArgmax">
                      {verdict
                        ? `Concluded — remaining observation VoI (${obs.length ? Math.max(...obs.map((a) => a.voi_score)).toFixed(2) : "0"}) below margin need`
                        : `Argmax → ${argmax ? `run probe ${argmax.action_id}` : "no executable action — conclude"}`}
                      <span className="voiFormula">  voi = eig − λ·cost − μ·risk  (λ=0.05, μ=1.0)</span>
                    </div>
                  </>
                );
              })()}
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
