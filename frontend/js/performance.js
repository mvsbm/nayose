const PREFIX = "nayose:";
function performanceApi() {
    return typeof globalThis.performance?.mark === "function" ? globalThis.performance : undefined;
}
export function markFrontend(name) {
    performanceApi()?.mark(`${PREFIX}${name}`);
}
export function measureFrontend(name, start, end) {
    const performance = performanceApi();
    if (!performance)
        return undefined;
    try {
        const measurement = performance.measure(`${PREFIX}${name}`, `${PREFIX}${start}`, `${PREFIX}${end}`);
        return measurement.duration;
    }
    catch {
        return undefined;
    }
}
