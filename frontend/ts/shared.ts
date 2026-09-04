// Server-driven shared lookups (no QueryPort needed).
export interface RunInfo {
  id: string;
  path: string;
  current?: boolean;
  created?: string;
  fingerprint?: string;
}

export async function listRuns(): Promise<RunInfo[]> {
  const res = await fetch("/api/runs");
  if (!res.ok) throw new Error(`/api/runs ${res.status}`);
  return (await res.json()) as RunInfo[];
}