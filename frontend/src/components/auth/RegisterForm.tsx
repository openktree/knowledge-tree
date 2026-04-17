"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/auth";
import { api } from "@/lib/api";
import { WaitlistForm } from "@/components/auth/WaitlistForm";

type Stage = "form" | "awaiting_verification";
type ResendState = "idle" | "sending" | "sent" | "error";

export function RegisterForm() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [registrationClosed, setRegistrationClosed] = useState<string | null>(null);
  const [checkingStatus, setCheckingStatus] = useState(true);
  const [verificationRequired, setVerificationRequired] = useState(false);
  const [stage, setStage] = useState<Stage>("form");
  const [resendState, setResendState] = useState<ResendState>("idle");
  const [resendMessage, setResendMessage] = useState<string | null>(null);

  useEffect(() => {
    api.auth
      .registrationStatus()
      .then((res) => {
        if (!res.registration_open) {
          setRegistrationClosed(res.reason ?? "Registration is currently disabled.");
        }
      })
      .catch(() => {
        // If we can't check, allow the form to show — server will enforce
      })
      .finally(() => setCheckingStatus(false));

    api.auth
      .authFeatures()
      .then((f) => setVerificationRequired(f.email_verification_required))
      .catch(() => {
        // default false — server is the source of truth anyway
      });
  }, []);

  if (checkingStatus) {
    return <p className="text-sm text-muted-foreground">Checking registration status...</p>;
  }

  if (registrationClosed) {
    return <WaitlistForm closedMessage={registrationClosed} />;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.auth.register({
        email,
        password,
        display_name: displayName || undefined,
      });
      if (verificationRequired) {
        setStage("awaiting_verification");
        return;
      }
      await login(email, password);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed.");
    } finally {
      setLoading(false);
    }
  }

  async function handleResend() {
    setResendState("sending");
    setResendMessage(null);
    try {
      await api.auth.requestVerifyToken(email);
      setResendState("sent");
      setResendMessage("Verification email sent — check your inbox.");
    } catch (err) {
      setResendState("error");
      setResendMessage(err instanceof Error ? err.message : "Failed to send verification email.");
    }
  }

  if (stage === "awaiting_verification") {
    return (
      <div className="flex flex-col gap-4">
        <h2 className="text-lg font-semibold">Check your email</h2>
        <p className="text-sm text-muted-foreground">
          We&apos;ve sent a verification link to <span className="font-medium">{email}</span>. The link
          expires in 24 hours. Click it to finish creating your account, then sign in.
        </p>
        <button
          type="button"
          onClick={handleResend}
          disabled={resendState === "sending" || resendState === "sent"}
          className="rounded-md border border-border px-4 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
        >
          {resendState === "sending"
            ? "Sending…"
            : resendState === "sent"
              ? "Sent"
              : "Resend verification email"}
        </button>
        {resendMessage && (
          <p className="text-xs text-muted-foreground">{resendMessage}</p>
        )}
        <p className="text-center text-sm text-muted-foreground">
          <a href="/login" className="underline hover:text-foreground">
            Back to sign in
          </a>
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <label htmlFor="display-name" className="text-sm font-medium">
          Display name (optional)
        </label>
        <input
          id="display-name"
          type="text"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="email" className="text-sm font-medium">
          Email
        </label>
        <input
          id="email"
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor="password" className="text-sm font-medium">
          Password
        </label>
        <input
          id="password"
          type="password"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </div>

      {error && <p className="text-sm text-red-500">{error}</p>}

      <button
        type="submit"
        disabled={loading}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        {loading ? "Creating account…" : "Create account"}
      </button>

      <p className="text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <a href="/login" className="underline hover:text-foreground">
          Sign in
        </a>
      </p>
    </form>
  );
}
