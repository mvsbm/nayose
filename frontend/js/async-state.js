export function asyncIdle() {
    return { kind: "idle" };
}
export function asyncLoading(requestId) {
    return { kind: "loading", requestId };
}
export function asyncReady(value) {
    return { kind: "ready", value };
}
export function asyncError(message, requestId) {
    return requestId === undefined ? { kind: "error", message } : { kind: "error", message, requestId };
}
export function isAsyncLoading(state) {
    return state.kind === "loading";
}
