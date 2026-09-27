export interface HealthMemory {
  available_bytes: number;
  total_bytes: number;
  process_rss_bytes: number;
  cgroup_limit_bytes: number | null;
}

export interface HealthAccelerator {
  kind: string;
  framework: string;
  name: string;
  available: boolean;
  total_memory_bytes: number | null;
  available_memory_bytes: number | null;
}

export interface HealthTelemetry {
  cpu_usage_percent: number | null;
  cpu_temperature_celsius: number | null;
  gpu_usage_percent: number | null;
  gpu_temperature_celsius: number | null;
  gpu_memory_total_bytes: number | null;
  gpu_memory_available_bytes: number | null;
}

export interface HealthResources {
  operating_system: string;
  machine: string;
  cpu_count: number;
  available_cpus: number;
  memory: HealthMemory;
  accelerators?: HealthAccelerator[];
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
  telemetry?: HealthTelemetry | null;
}

export interface HealthConfiguration {
  hash: string;
  schema_version: number;
  source: string;
  telemetry_interval_seconds?: number;
}
