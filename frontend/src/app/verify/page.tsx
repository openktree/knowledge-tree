"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/auth";

type Status = "loading" | "success" | "error" | "no-token";

export default function VerifyPage() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const { refreshUser } = useAuth();
  const [status, setStatus] = useState<Status>(token ? "loading" : "no-token");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;

    let cancelled = false;

    void (async () => {
      try {
        await api.auth.verify(token);
        if (cancelled) return;
        setStatus("success");
        await refreshUser();
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setErrorMessage(err instanceof Error ? err.message : "Verification failed");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [token, refreshUser]);

  return (
    <div className="max-w-md mx-auto px-6 py-20 text-center flex flex-col gap-4">
      {status === "loading" && (
        <p className="text-sm text-muted-foreground">Verifying your account...</p>
      )}

      {status === "success" && (
        <>
          <h1 className="text-xl font-semibold">Account verified</h1>
          <p className="text-sm text-muted-foreground">
            Your email has been verified successfully.
          </p>
          <Link href="/profile" className="text-sm text-primary hover:underline">
            Go to profile
          </Link>
        </>
      )}

      {status === "error" && (
        <>
          <h1 className="text-xl font-semibold">Verification failed</h1>
          <p className="text-sm text-muted-foreground">
            {errorMessage ?? "Something went wrong."}
          </p>
          <Link href="/profile" className="text-sm text-primary hover:underline">
            Back to profile
          </Link>
        </>
      )}

      {status === "no-token" && (
        <>
          <h1 className="text-xl font-semibold">Missing verification token</h1>
          <p className="text-sm text-muted-foreground">
            No verification token was provided. Please check your email for the verification link.
          </p>
          <Link href="/profile" className="text-sm text-primary hover:underline">
            Back to profile
          </Link>
        </>
      )}
    </div>
  );
}
