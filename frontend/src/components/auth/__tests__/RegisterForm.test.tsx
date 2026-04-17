import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RegisterForm } from "../RegisterForm";

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

const registerMock = vi.fn();
const registrationStatusMock = vi.fn();
const requestVerifyTokenMock = vi.fn();
const authFeaturesMock = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    auth: {
      register: (data: unknown) => registerMock(data),
      registrationStatus: () => registrationStatusMock(),
      authFeatures: () => authFeaturesMock(),
      requestVerifyToken: (email: string) => requestVerifyTokenMock(email),
    },
  },
}));

vi.mock("@/components/auth/WaitlistForm", () => ({
  WaitlistForm: () => <div>waitlist</div>,
}));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("RegisterForm — awaiting verification flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    registrationStatusMock.mockResolvedValue({ registration_open: true });
  });

  async function waitForForm() {
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /create account/i })).toBeInTheDocument();
    });
  }

  async function fillAndSubmit(email: string, password: string) {
    const user = userEvent.setup();
    await user.type(screen.getByLabelText(/email/i), email);
    await user.type(screen.getByLabelText(/password/i), password);
    await user.click(screen.getByRole("button", { name: /create account/i }));
    return user;
  }

  it("shows 'check your email' screen when verification is required", async () => {
    authFeaturesMock.mockResolvedValue({
      google_oauth_enabled: false,
      email_verification_enabled: true,
      email_verification_required: true,
    });
    registerMock.mockResolvedValue({});

    render(<RegisterForm />);
    await waitForForm();
    await fillAndSubmit("alice@example.com", "hunter2!!");

    await waitFor(() => {
      expect(screen.getByText(/check your email/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/alice@example.com/)).toBeInTheDocument();
    expect(loginMock).not.toHaveBeenCalled();
    expect(pushMock).not.toHaveBeenCalled();
  });

  it("auto-logs in when verification is NOT required", async () => {
    authFeaturesMock.mockResolvedValue({
      google_oauth_enabled: false,
      email_verification_enabled: false,
      email_verification_required: false,
    });
    registerMock.mockResolvedValue({});
    loginMock.mockResolvedValue(undefined);

    render(<RegisterForm />);
    await waitForForm();
    await fillAndSubmit("alice@example.com", "hunter2!!");

    await waitFor(() => {
      expect(loginMock).toHaveBeenCalledWith("alice@example.com", "hunter2!!");
    });
    expect(pushMock).toHaveBeenCalledWith("/");
  });

  it("resend button on awaiting-verification screen calls requestVerifyToken", async () => {
    authFeaturesMock.mockResolvedValue({
      google_oauth_enabled: false,
      email_verification_enabled: true,
      email_verification_required: true,
    });
    registerMock.mockResolvedValue({});
    requestVerifyTokenMock.mockResolvedValue(undefined);

    render(<RegisterForm />);
    await waitForForm();
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
});
