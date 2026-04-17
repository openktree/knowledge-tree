/**
 * Pull fastapi-users error code from a thrown API error.
 *
 * `request()` in lib/api.ts throws `Error("API error 400 ...: {body}")`
 * where the body is the raw response text. For fastapi-users that looks
 * like `{"detail": "LOGIN_USER_NOT_VERIFIED"}` or a longer object for
 * validation errors. This helper returns the string detail when present,
 * or null otherwise, without caring about the surrounding wrapper.
 */
export function parseAuthErrorCode(err: unknown): string | null {
  if (!(err instanceof Error)) return null;
  const match = err.message.match(/"detail"\s*:\s*"([^"]+)"/);
  return match ? match[1] : null;
}
