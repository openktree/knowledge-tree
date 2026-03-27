"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/auth";
import { api } from "@/lib/api";

export default function InviteRedemptionPage() {
  const { login } = useAuth();
  const router = useRouter();

  // Step 1: validate invite
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [validating, setValidating] = useState(false);
  const [validateError, setValidateError] = useState<string | null>(null);

  // Step 2: register
  const [validated, setValidated] = useState(false);
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);

  async function handleValidate(e: React.FormEvent) {
    e.preventDefault();
    setValidateError(null);
    setValidating(true);
    try {
      const result = await api.invites.validate(email, code);
      if (result.valid) {
        setEmail(result.email);
        setValidated(true);
      } else {
        setValidateError(
          "Invalid or expired invite code. Please check and try again.",
        );
      }
    } catch (err) {
      setValidateError(
        err instanceof Error ? err.message : "Validation failed.",
      );
    } finally {
      setValidating(false);
    }
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setRegisterError(null);
    setRegistering(true);
    try {
      await api.auth.register({
        email,
        password,
        display_name: displayName || undefined,
      });
      await login(email, password);
      router.push("/");
    } catch (err) {
      setRegisterError(
        err instanceof Error ? err.message : "Registration failed.",
      );
    } finally {
      setRegistering(false);
    }
  }

  if (!validated) {
    return (
      <>
        <h1 className="mb-6 text-xl font-semibold">Redeem invite</h1>
        <form onSubmit={handleValidate} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label htmlFor="inv-email" className="text-sm font-medium">
              Email
            </label>
            <input
              id="inv-email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label htmlFor="inv-code" className="text-sm font-medium">
              Invite code
            </label>
            <input
              id="inv-code"
              type="text"
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>

          {validateError && (
            <p className="text-sm text-red-500">{validateError}</p>
          )}

          <button
            type="submit"
            disabled={validating}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {validating ? "Validating..." : "Validate invite"}
          </button>

          <div className="flex flex-col gap-2 items-center text-center">
            <a
              href="/register"
              className="text-sm underline hover:text-foreground"
            >
              Back to registration
            </a>
            <a
              href="/login"
              className="text-sm underline hover:text-foreground"
            >
              Back to sign in
            </a>
          </div>
        </form>
      </>
    );
  }

  return (
    <>
      <h1 className="mb-6 text-xl font-semibold">Create your account</h1>
      <p className="mb-4 text-sm text-muted-foreground">
        Invite validated for <span className="font-medium">{email}</span>
      </p>

      <form onSubmit={handleRegister} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor="reg-email" className="text-sm font-medium">
            Email
          </label>
          <input
            id="reg-email"
            type="email"
            value={email}
            readOnly
            className="rounded-md border border-border bg-muted px-3 py-2 text-sm cursor-not-allowed"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="reg-name" className="text-sm font-medium">
            Display name (optional)
          </label>
          <input
            id="reg-name"
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="reg-password" className="text-sm font-medium">
            Password
          </label>
          <input
            id="reg-password"
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        {registerError && (
          <p className="text-sm text-red-500">{registerError}</p>
        )}

        <button
          type="submit"
          disabled={registering}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {registering ? "Creating account..." : "Create account"}
        </button>
      </form>
    </>
  );
}
