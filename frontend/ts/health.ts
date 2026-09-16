export interface HealthMemory {
  available_bytes: number;
  total_bytes: number;
  process_rss_bytes: number;
  cgroup_limit_bytes: number | null;
}

export interface HealthResources {
  operating_system: string;
  machine: string;
  cpu_count: number;
  available_cpus: number;
  memory: HealthMemory;
  storage?: {
    path: string;
    available_bytes: number;
    total_bytes: number;
    reserve_bytes?: number;
  };
  policy?: {
    device: string;
    process_workers: number;
    cpu_threads: number | null;
    model_threads: number | null;
    embedding_batch_size: number;
  };
}

export interface HealthConfiguration {
  hash: string;
  schema_version: number;
  source: string;
}
