import { useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { HarnessDoc } from "./types";

// Cisco palette values used inside Recharts (Recharts wants JS strings, not vars).
const C_GREEN = "#27AE60";
const C_CRITICAL = "#E74C3C";
const C_BLUE = "#049FD9";
const C_GRID = "#E1E6EB";
const C_TEXT_DIM = "#85929E";
const C_TEXT = "#566573";

export default function MetricsPanel() {
  const [doc, setDoc] = useState<HarnessDoc | null>(null);
  useEffect(() => {
    fetch("/harness").then((r) => r.json()).then(setDoc).catch(() => {});
  }, []);
  if (!doc) return <div className="chartCard">loading harness_results.json…</div>;
  const s = doc.summary;

  const perSlice = Object.entries(s.per_slice).map(([slice, v]) => ({
    slice: slice.replace(/_/g, " "),
    ON: Math.round(v.probes_on * 100),
    OFF: Math.round(v.probes_off * 100),
  }));

  const reliability = s.reliability_bins.map((b) => ({
    predicted: Math.round(b.predicted * 100),
    empirical: Math.round(b.empirical * 100),
    n: b.n,
  }));
  const diag = [{ predicted: 0, ideal: 0 }, { predicted: 100, ideal: 100 }];

  return (
    <div className="metrics">
      {/* KPI header row — the headline read-out */}
      <div className="kpiRow">
        <Kpi cat="Δ Accuracy (hard slices)"
             value={`+${Math.round(s.headline_ablation.delta * 100)}%`}
             sub={`${Math.round(s.headline_ablation.probes_off * 100)}% → ${Math.round(s.headline_ablation.probes_on * 100)}%`}
             tone="good" />
        <Kpi cat="Top-1 · Policy ON"
             value={`${Math.round(s.top1_accuracy.probes_on * 100)}%`}
             sub={`of ${doc.runs.filter((r: any) => r.probes_enabled).length} runs`}
             tone="good" />
        <Kpi cat="FP Rollback · Adversarial"
             value={`${Math.round(s.false_positive_rollback_rate.adversarial_off * 100)}% → ${Math.round(s.false_positive_rollback_rate.adversarial_on * 100)}%`}
             sub="off → on, adversarial slice"
             tone="good" />
        <Kpi cat="Brier Score · ON"
             value={s.brier.probes_on.toFixed(3)}
             sub={`baseline ${s.brier.probes_off.toFixed(3)}`}
             tone="good" />
      </div>

      {/* Charts */}
      <div className="charts">
        <div className="chartCard">
          <h3>Top-1 Accuracy by Slice · Policy ON vs OFF</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={perSlice}>
              <CartesianGrid strokeDasharray="3 3" stroke={C_GRID} />
              <XAxis dataKey="slice" stroke={C_TEXT} fontSize={11} />
              <YAxis domain={[0, 100]} stroke={C_TEXT} fontSize={11} unit="%" />
              <Tooltip contentStyle={{ background: "#FFFFFF", border: `1px solid ${C_GRID}`, fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="ON" fill={C_GREEN} radius={[3, 3, 0, 0]} />
              <Bar dataKey="OFF" fill={C_CRITICAL} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="chartCard">
          <h3>Reliability Diagram · Predicted Confidence vs Empirical Accuracy</h3>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart>
              <CartesianGrid strokeDasharray="3 3" stroke={C_GRID} />
              <XAxis type="number" dataKey="predicted" domain={[0, 100]} stroke={C_TEXT} fontSize={11} unit="%" name="predicted" />
              <YAxis type="number" domain={[0, 100]} stroke={C_TEXT} fontSize={11} unit="%" />
              <Tooltip contentStyle={{ background: "#FFFFFF", border: `1px solid ${C_GRID}`, fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line data={diag} dataKey="ideal" stroke={C_TEXT_DIM} strokeDasharray="6 4" dot={false} name="perfect" />
              <Line data={reliability} dataKey="empirical" stroke={C_BLUE} strokeWidth={2} dot={{ r: 4, fill: C_BLUE }} name="observed" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <p className="disclaimer">
        Eight authored bundles across five slices; numbers are illustrative, not publication-grade.
        The headline is the sign and size of the delta, reproducible offline with <code>python -m ic.harness</code>.
      </p>
    </div>
  );
}

function Kpi({ cat, value, sub, tone }: { cat: string; value: string; sub?: string; tone?: "good" | "bad" }) {
  return (
    <div className={`kpi ${tone === "good" ? "good" : tone === "bad" ? "bad" : ""}`}>
      <div className="kpiCat">{cat}</div>
      <div className="kpiValue">{value}</div>
      {sub && <div className="kpiSub">{sub}</div>}
    </div>
  );
}
