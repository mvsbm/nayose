const PREFIX = "nayose:";

function performanceApi(): Performance | undefined {
  return typeof globalThis.performance?.mark === "function" ? globalThis.performance : undefined;
}

export function markFrontend(name: string): void {
  performanceApi()?.mark(`${PREFIX}${name}`);
}

export function measureFrontend(name: string, start: string, end: string): number | undefined {
  const performance = performanceApi();
  if (!performance) return undefined;
  try {
    const measurement = performance.measure(`${PREFIX}${name}`, `${PREFIX}${start}`, `${PREFIX}${end}`);
    return measurement.duration;
  } catch {
    return undefined;
  }
}
