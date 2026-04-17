"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/auth";
import { api } from "@/lib/api";
import { parseAuthErrorCode } from "@/lib/auth-errors";

type ResendState = "idle" | "sending" | "sent" | "error";

export function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [googleEnabled, setGoogleEnabled] = useState(false);
  const [needsVerification, setNeedsVerification] = useState(false);
  const [resendState, setResendState] = useState<ResendState>("idle");
  const [resendMessage, setResendMessage] = useState<string | null>(null);

  useEffect(() => {
    api.auth.authFeatures().then((f) => setGoogleEnabled(f.google_oauth_enabled)).catch(() => {});
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNeedsVerification(false);
    setResendState("idle");
    setResendMessage(null);
    setLoading(true);
    try {
      await login(email, password);
      router.push("/");
    } catch (err) {
      if (parseAuthErrorCode(err) === "LOGIN_USER_NOT_VERIFIED") {
        setNeedsVerification(true);
        setError("Please verify your email before signing in.");
      } else {
        setError("Invalid email or password.");
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleResend() {
    if (!email) {
      setResendMessage("Enter your email above first.");
      setResendState("error");
      return;
    }
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

  async function handleGoogle() {
    try {
      const callbackUrl = `${window.location.origin}/auth/callback`;
      const { authorization_url } = await api.auth.googleAuthorize(callbackUrl);
      window.location.href = authorization_url;
    } catch {
      setError("Google OAuth unavailable.");
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <label htmlFor="email" className="text-sm font-medium">
          Email
        </label>
        <input
          id="email"
          type="email"
          required
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            if (resendState === "sent" || resendState === "error") {
              setResendState("idle");
              setResendMessage(null);
            }
          }}
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
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </div>

      {error && <p className="text-sm text-red-500">{error}</p>}

      {needsVerification && (
        <div className="flex flex-col gap-2 rounded-md border border-border bg-muted/50 px-3 py-3">
          <p className="text-xs text-muted-foreground">
            We sent a verification link to your email when you registered. Didn&apos;t get it or did it expire?
          </p>
          <button
            type="button"
            onClick={handleResend}
            disabled={resendState === "sending" || resendState === "sent"}
            className="rounded-md border border-border px-3 py-2 text-sm font-medium hover:bg-accent disabled:opacity-50"
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
        </div>
      )}

      <button
        type="submit"
        disabled={loading}
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        {loading ? "Signing in…" : "Sign in"}
      </button>

      {googleEnabled && (
        <>
          <div className="relative my-2 flex items-center">
            <div className="flex-1 border-t border-border" />
            <span className="mx-3 text-xs text-muted-foreground">or</span>
            <div className="flex-1 border-t border-border" />
          </div>

          <button
            type="button"
            onClick={handleGoogle}
            className="rounded-md border border-border px-4 py-2 text-sm font-medium hover:bg-accent"
          >
            Continue with Google
          </button>
        </>
      )}

      <p className="text-center text-sm text-muted-foreground">
        No account?{" "}
        <a href="/register" className="underline hover:text-foreground">
          Register
        </a>
      </p>
    </form>
  );
}
