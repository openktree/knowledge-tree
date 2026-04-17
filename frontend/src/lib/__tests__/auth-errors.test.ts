import { describe, it, expect } from "vitest";
import { parseAuthErrorCode } from "../auth-errors";

describe("parseAuthErrorCode", () => {
  it("extracts fastapi-users detail string from wrapped API error", () => {
    const err = new Error(
      'API error 400 Bad Request: {"detail":"LOGIN_USER_NOT_VERIFIED"}',
    );
    expect(parseAuthErrorCode(err)).toBe("LOGIN_USER_NOT_VERIFIED");
  });

  it("handles detail with whitespace after colon", () => {
    const err = new Error('400: {"detail" : "LOGIN_BAD_CREDENTIALS"}');
    expect(parseAuthErrorCode(err)).toBe("LOGIN_BAD_CREDENTIALS");
  });

  it("returns null when no detail field present", () => {
    const err = new Error("Network error");
    expect(parseAuthErrorCode(err)).toBeNull();
  });

  it("returns null for non-Error values", () => {
    expect(parseAuthErrorCode(null)).toBeNull();
    expect(parseAuthErrorCode("a string")).toBeNull();
    expect(parseAuthErrorCode({ message: "not an error" })).toBeNull();
  });

  it("returns null when detail is an object (validation errors)", () => {
    const err = new Error('{"detail": [{"loc": ["body"], "msg": "..."}]}');
    expect(parseAuthErrorCode(err)).toBeNull();
  });
});
