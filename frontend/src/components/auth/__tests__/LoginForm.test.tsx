import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LoginForm } from "../LoginForm";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const pushMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

const loginMock = vi.fn();

vi.mock("@/contexts/auth", () => ({
  useAuth: () => ({ login: loginMock }),
}));

const requestVerifyTokenMock = vi.fn();
const authFeaturesMock = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    auth: {
      authFeatures: () => authFeaturesMock(),
      requestVerifyToken: (email: string) => requestVerifyTokenMock(email),
    },
  },
}));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("LoginForm — verification flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authFeaturesMock.mockResolvedValue({
      google_oauth_enabled: false,
      email_verification_enabled: true,
      email_verification_required: true,
    });
  });

  async function fillAndSubmit(email: string, password: string) {
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/email/i), email);
    await user.type(screen.getByLabelText(/password/i), password);
    await user.click(screen.getByRole("button", { name: /sign in/i }));
    return user;
  }

  it("shows resend button when login fails with LOGIN_USER_NOT_VERIFIED", async () => {
    loginMock.mockRejectedValue(
      new Error("API error 400 Bad Request: {\"detail\":\"LOGIN_USER_NOT_VERIFIED\"}"),
    );
    render(<LoginForm />);

    await fillAndSubmit("alice@example.com", "hunter2!!");

    await waitFor(() => {
      expect(screen.getByText(/verify your email/i)).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: /resend verification email/i }),
    ).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("does not show resend button on generic auth failure", async () => {
    loginMock.mockRejectedValue(
      new Error("API error 400 Bad Request: {\"detail\":\"LOGIN_BAD_CREDENTIALS\"}"),
    );
    render(<LoginForm />);

    await fillAndSubmit("alice@example.com", "wrongpass");

    await waitFor(() => {
      expect(screen.getByText(/invalid email or password/i)).toBeInTheDocument();
    });
    expect(
      screen.queryByRole("button", { name: /resend verification email/i }),
    ).not.toBeInTheDocument();
  });

  it("clicking resend calls requestVerifyToken with the entered email", async () => {
    loginMock.mockRejectedValue(
      new Error("API error 400 Bad Request: {\"detail\":\"LOGIN_USER_NOT_VERIFIED\"}"),
    );
    requestVerifyTokenMock.mockResolvedValue(undefined);

    render(<LoginForm />);
    const user = await fillAndSubmit("alice@example.com", "hunter2!!");

    const resendBtn = await screen.findByRole("button", {
      name: /resend verification email/i,
    });
    await user.click(resendBtn);

    await waitFor(() => {
      expect(requestVerifyTokenMock).toHaveBeenCalledWith("alice@example.com");
    });
    expect(
      await screen.findByText(/verification email sent/i),
    ).toBeInTheDocument();
  });

  it("routes to home on successful login", async () => {
    loginMock.mockResolvedValue(undefined);
    render(<LoginForm />);

    await fillAndSubmit("alice@example.com", "hunter2!!");

    await waitFor(() => {
      expect(pushMock).toHaveBeenCalledWith("/");
    });
  });
});
