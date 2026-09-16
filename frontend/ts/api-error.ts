export class ApiError extends Error {
  readonly status?: number;
  readonly requestId?: string;

  constructor(message: string, options: { status?: number; requestId?: string } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.requestId = options.requestId;
  }
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError && error.requestId) return `${error.message} [${error.requestId}]`;
  return error instanceof Error ? error.message : String(error);
}

export function errorKey(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function presentError(error: unknown, translate: (key: string) => string): string {
  const key = errorKey(error);
  const localized = translate(key);
  if (!(error instanceof ApiError) || !error.requestId) return localized === key ? errorText(error) : localized;
  return `${localized === key ? errorText(error) : localized} [${error.requestId}]`;
}
