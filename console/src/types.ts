export type ICEvent = {
  type: string;
  incident_id?: string;
  probes_enabled?: boolean;
  [k: string]: any;
};

export type Incident = {
  incident_id: string;
  slice: string;
  title: string;
  alert: Record<string, any>;
  ground_truth: Record<string, any>;
  has_replay: boolean;
};

export type Hypothesis = {
  id: string;
  claim: string;
  agent_id: string;
  posterior: number;
  eliminated: boolean;
  eliminated_reason?: string;
  evidence_refs: string[];
};

export type EvidenceLeaf = { uri: string; hash: string; probe?: boolean; intervention?: boolean };

export type HarnessDoc = {
  runs: any[];
  summary: {
    top1_accuracy: { probes_on: number; probes_off: number };
    headline_ablation: { slices: string[]; probes_on: number; probes_off: number; delta: number };
    per_slice: Record<string, { probes_on: number; probes_off: number }>;
    false_positive_rollback_rate: Record<string, number>;
    brier: { probes_on: number; probes_off: number; all: number };
    reliability_bins: { bin: string; mid: number; n: number; predicted: number; empirical: number }[];
  };
};

export const AGENTS = ["change_agent", "telemetry_agent", "history_agent"] as const;
