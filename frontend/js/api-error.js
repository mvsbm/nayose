export class ApiError extends Error {
    status;
    requestId;
    constructor(message, options = {}) {
        super(message);
        this.name = "ApiError";
        this.status = options.status;
        this.requestId = options.requestId;
    }
}
export function errorText(error) {
    if (error instanceof ApiError && error.requestId)
        return `${error.message} [${error.requestId}]`;
    return error instanceof Error ? error.message : String(error);
}
export function errorKey(error) {
    return error instanceof Error ? error.message : String(error);
}
export function presentError(error, translate) {
    const key = errorKey(error);
    const localized = translate(key);
    if (!(error instanceof ApiError) || !error.requestId)
        return localized === key ? errorText(error) : localized;
    return `${localized === key ? errorText(error) : localized} [${error.requestId}]`;
}
