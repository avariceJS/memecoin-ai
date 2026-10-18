export type ApiErrorCode =
  | "HTTP_ERROR"
  | "NON_JSON_RESPONSE"
  | "SCHEMA_VALIDATION_FAILED"
  | "UNSUPPORTED_API_MAJOR"
  | "FETCH_FAILED"
  | "DISALLOWED_URL";

export class ControlCenterApiError extends Error {
  readonly code: ApiErrorCode;
  readonly httpStatus?: number;

  constructor(code: ApiErrorCode, httpStatus?: number) {
    super(code);
    this.name = "ControlCenterApiError";
    this.code = code;
    this.httpStatus = httpStatus;
  }
}
