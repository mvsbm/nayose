export type AsyncState<T> =
  | { readonly kind: "idle" }
  | { readonly kind: "loading"; readonly requestId: string }
  | { readonly kind: "ready"; readonly value: T }
  | { readonly kind: "error"; readonly message: string; readonly requestId?: string };

export function asyncIdle<T>(): AsyncState<T> {
  return { kind: "idle" };
}

export function asyncLoading<T>(requestId: string): AsyncState<T> {
  return { kind: "loading", requestId };
}

export function asyncReady<T>(value: T): AsyncState<T> {
  return { kind: "ready", value };
}

export function asyncError<T>(message: string, requestId?: string): AsyncState<T> {
  return requestId === undefined ? { kind: "error", message } : { kind: "error", message, requestId };
}

export function isAsyncLoading<T>(state: AsyncState<T>): state is Extract<AsyncState<T>, { kind: "loading" }> {
  return state.kind === "loading";
}
