import type { HealthResources } from "./health.js";
import { esc } from "./schema.js";
import type { Store } from "./store.js";

export const formatPercent = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? "—" : `${value.toFixed(0)}%`;

export const formatTemperature = (value: number | null | undefined): string =>
  value == null || !Number.isFinite(value) ? "—" : `${value.toFixed(0)}°C`;

export const formatBytes = (value: number): string => {
  if (!Number.isFinite(value) || value < 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
};

export const renderResourceStatus = (resources: HealthResources | undefined, store: Store): string => {
  const memory = resources?.memory;
  const storage = resources?.storage;
  const policy = resources?.policy;
  const telemetry = resources?.telemetry;
  const gpuAccelerator = resources?.accelerators?.find(
    accelerator => accelerator.kind === "cuda" || accelerator.kind === "rocm",
  );
  const gpuIsUnified = policy?.device === "mps" || policy?.device === "mlx_gpu";
  const gpuMemoryLabel = gpuIsUnified ? store.t("sharedMemory") : store.t("vramAvailable");
  const gpuMemoryBytes = telemetry?.gpu_memory_available_bytes ?? (
    gpuIsUnified ? memory?.available_bytes : gpuAccelerator?.available_memory_bytes
  );
  const metric = (
    label: string,
    value: number | null | undefined,
    format: (value: number) => string,
  ): string => value == null || !Number.isFinite(value) ? "" : ` · ${esc(label)} ${esc(format(value))}`;
  const cpu = resources
    ? `<span class="status-bar-item" data-resource="cpu">${esc(store.t("cpu"))} ${esc(`${resources.available_cpus}/${resources.cpu_count}`)}${metric(store.t("cpuUsage"), telemetry?.cpu_usage_percent, value => `${value.toFixed(0)}%`)}${metric(store.t("temperature"), telemetry?.cpu_temperature_celsius, value => `${value.toFixed(0)}°C`)}</span>`
    : "";
  const gpu = policy?.device || telemetry?.gpu_usage_percent != null || gpuMemoryBytes != null
    ? `<span class="status-bar-item" data-resource="gpu">${esc(store.t("gpu"))}${policy?.device ? ` ${esc(policy.device)}` : ""}${metric(store.t("gpuUsage"), telemetry?.gpu_usage_percent, value => `${value.toFixed(0)}%`)}${metric(store.t("temperature"), telemetry?.gpu_temperature_celsius, value => `${value.toFixed(0)}°C`)}${metric(gpuMemoryLabel, gpuMemoryBytes, formatBytes)}</span>`
    : "";
  const ram = memory
    ? `<span class="status-bar-item" data-resource="ram">${esc(store.t("ramAvailable"))} ${esc(formatBytes(memory.available_bytes))}</span>`
    : "";
  const storageWarning = storage && storage.reserve_bytes !== undefined && storage.available_bytes <= storage.reserve_bytes ? " ⚠" : "";
  const storageItem = storage
    ? `<span class="status-bar-item" data-resource="storage">${esc(store.t("storageAvailable"))} ${esc(formatBytes(storage.available_bytes))}${storageWarning}</span>`
    : "";
  return [cpu, gpu, ram, storageItem].filter(Boolean).join("");
};
