export interface EvaluationMetric {
  id: string;
  value: number;
  unit: "ratio" | "count";
}

export interface EvaluationOutcome {
  id: string;
  count: number;
  rate: number;
}
