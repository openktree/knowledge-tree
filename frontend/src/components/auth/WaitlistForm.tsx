"use client";

import { useState } from "react";
import { api } from "@/lib/api";

interface WaitlistFormProps {
  closedMessage: string;
}

export function WaitlistForm({ closedMessage }: WaitlistFormProps) {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.waitlist.submit({
        email,
        display_name: displayName || undefined,
        message: message || undefined,
      });
      setSubmitted(true);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to submit request.",
      );
    } finally {
      setLoading(false);
    }
  }

  if (submitted) {
    return (
      <div className="flex flex-col gap-4 items-center text-center">
        <p className="text-sm text-muted-foreground">
          Your request has been submitted. You will receive an invite code when
          approved.
        </p>
        <a
          href="/register/invite"
          className="text-sm underline hover:text-foreground"
        >
          Already have an invite code?
        </a>
        <a href="/login" className="text-sm underline hover:text-foreground">
          Back to sign in
        </a>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground text-center">
        {closedMessage}
      </p>
      <p className="text-sm text-muted-foreground text-center">
        Request access by filling out the form below.
      </p>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor="wl-email" className="text-sm font-medium">
            Email
          </label>
          <input
            id="wl-email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="wl-name" className="text-sm font-medium">
            Display name (optional)
          </label>
          <input
            id="wl-name"
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label htmlFor="wl-message" className="text-sm font-medium">
            Why do you want access? (optional)
          </label>
          <textarea
            id="wl-message"
            rows={3}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            className="rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring resize-none"
          />
        </div>

        {error && <p className="text-sm text-red-500">{error}</p>}

        <button
          type="submit"
          disabled={loading}
          className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {loading ? "Submitting..." : "Request access"}
        </button>
      </form>

      <div className="flex flex-col gap-2 items-center text-center">
        <a
          href="/register/invite"
          className="text-sm underline hover:text-foreground"
        >
          Already have an invite code?
        </a>
        <a href="/login" className="text-sm underline hover:text-foreground">
          Back to sign in
        </a>
      </div>
    </div>
  );
}
