import { useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { HarnessDoc } from "./types";

export default function MetricsPanel() {
  const [doc, setDoc] = useState<HarnessDoc | null>(null);
  useEffect(() => {
    fetch("/harness").then((r) => r.json()).then(setDoc).catch(() => {});
  }, []);
  if (!doc) return <div className="pane"><div className="idle">loading harness_results.json…</div></div>;
  const s = doc.summary;

  const perSlice = Object.entries(s.per_slice).map(([slice, v]) => ({
    slice: slice.replace("_", " "),
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
      <div className="headline">
        <div className="big">
          <div className="bigNum">{Math.round(s.headline_ablation.delta * 100)}%</div>
          <div className="bigLbl">top-1 accuracy delta on<br />{s.headline_ablation.slices.join(" + ")}</div>
        </div>
        <div className="kpis">
          <Kpi label="accuracy · probes ON" value={`${Math.round(s.top1_accuracy.probes_on * 100)}%`} good />
          <Kpi label="accuracy · probes OFF" value={`${Math.round(s.top1_accuracy.probes_off * 100)}%`} />
          <Kpi label="false-positive rollback · OFF (adversarial)" value={`${Math.round(s.false_positive_rollback_rate.adversarial_off * 100)}%`} />
          <Kpi label="false-positive rollback · ON (adversarial)" value={`${Math.round(s.false_positive_rollback_rate.adversarial_on * 100)}%`} good />
          <Kpi label="Brier · ON" value={s.brier.probes_on.toFixed(3)} good />
          <Kpi label="Brier · OFF" value={s.brier.probes_off.toFixed(3)} />
        </div>
      </div>

      <div className="charts">
        <div className="chartCard">
          <h3>Ablation — top-1 accuracy by slice (probes ON vs OFF)</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={perSlice}>
              <CartesianGrid strokeDasharray="3 3" stroke="#233" />
              <XAxis dataKey="slice" stroke="#8aa" fontSize={12} />
              <YAxis domain={[0, 100]} stroke="#8aa" fontSize={12} unit="%" />
              <Tooltip contentStyle={{ background: "#0d1520", border: "1px solid #2a3a4a" }} />
              <Legend />
              <Bar dataKey="ON" fill="#39d98a" radius={[4, 4, 0, 0]} />
              <Bar dataKey="OFF" fill="#e5533c" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="chartCard">
          <h3>Reliability diagram (predicted confidence vs empirical accuracy)</h3>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart>
              <CartesianGrid strokeDasharray="3 3" stroke="#233" />
              <XAxis type="number" dataKey="predicted" domain={[0, 100]} stroke="#8aa" fontSize={12} unit="%" name="predicted" />
              <YAxis type="number" domain={[0, 100]} stroke="#8aa" fontSize={12} unit="%" />
              <Tooltip contentStyle={{ background: "#0d1520", border: "1px solid #2a3a4a" }} />
              <Line data={diag} dataKey="ideal" stroke="#456" strokeDasharray="6 4" dot={false} name="perfect" />
              <Line data={reliability} dataKey="empirical" stroke="#5ac8fa" strokeWidth={2} dot={{ r: 4 }} name="observed" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <p className="disclaimer">
        Three bundles — numbers are illustrative, not publication-grade. The point is the
        sign and size of the ablation delta, reproducible offline via <code>python -m ic.harness</code>.
      </p>
    </div>
  );
}

function Kpi({ label, value, good }: { label: string; value: string; good?: boolean }) {
  return (
    <div className={`kpi ${good ? "good" : ""}`}>
      <div className="kval">{value}</div>
      <div className="klbl">{label}</div>
    </div>
  );
}
