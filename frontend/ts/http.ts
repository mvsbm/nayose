import { ApiError } from "./api-error.js";

export async function fetchJson(path: string, init?: RequestInit): Promise<unknown> {
  const controller = new AbortController();
  const externalSignal = init?.signal;
  const abortFromCaller = (): void => controller.abort();
  if (externalSignal?.aborted) controller.abort();
  else externalSignal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeout = setTimeout(() => controller.abort(), 20000);
  const headers = new Headers(init?.headers ?? {});
  try {
    const token = localStorage.getItem("nayose.token");
    if (token) headers.set("Authorization", `Bearer ${token}`);
  } catch {
  }
  try {
    const response = await fetch(path, { ...init, headers, signal: controller.signal });
    if (!response.ok) {
      const detail = await response.json().catch(() => null);
      throw new ApiError(
        typeof detail?.detail === "string" ? detail.detail : `HTTP ${response.status}`,
      );
    }
    return await response.json();
  } catch (error) {
    if (externalSignal?.aborted) throw new ApiError("request_cancelled");
    if (controller.signal.aborted) throw new ApiError("request_timeout");
    throw error;
  } finally {
    clearTimeout(timeout);
    externalSignal?.removeEventListener("abort", abortFromCaller);
  }
}
