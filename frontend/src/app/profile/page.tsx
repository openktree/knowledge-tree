"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { KeyRound, ExternalLink } from "lucide-react";
import { useAuth } from "@/contexts/auth";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function ProfilePage() {
  const { user } = useAuth();
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [hasKey, setHasKey] = useState(user?.has_api_key ?? false);
  const [showForm, setShowForm] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [emailVerificationEnabled, setEmailVerificationEnabled] = useState(false);
  const [verifyRequesting, setVerifyRequesting] = useState(false);
  const [verifyMessage, setVerifyMessage] = useState<string | null>(null);
  const [verifySent, setVerifySent] = useState(false);

  useEffect(() => {
    void api.auth.authFeatures().then((f) => {
      setEmailVerificationEnabled(f.email_verification_enabled);
    }).catch(() => {
      // ignore — feature flag unavailable
    });
  }, []);

  if (!user) return null;

  const handleRequestVerify = async () => {
    setVerifyRequesting(true);
    setVerifyMessage(null);
    try {
      await api.auth.requestVerifyToken(user.email);
      setVerifySent(true);
      setVerifyMessage("Verification email sent — check your inbox.");
    } catch (err) {
      setVerifyMessage(err instanceof Error ? err.message : "Failed to send verification email");
    } finally {
      setVerifyRequesting(false);
    }
  };

  const handleSaveKey = async () => {
    if (!apiKey.trim()) return;
    setSaving(true);
    setMessage(null);
    try {
      const res = await api.byok.set(apiKey.trim());
      setHasKey(res.has_key);
      setApiKey("");
      setShowForm(false);
      setMessage("API key saved successfully");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to save key");
    } finally {
      setSaving(false);
    }
  };

  const handleRemoveKey = async () => {
    setRemoving(true);
    setMessage(null);
    try {
      const res = await api.byok.remove();
      setHasKey(res.has_key);
      setMessage("API key removed");
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Failed to remove key");
    } finally {
      setRemoving(false);
    }
  };

  const showVerifyButton = emailVerificationEnabled && !user.is_verified && !verifySent;

  const rows: { label: string; value: React.ReactNode }[] = [
    { label: "Display name", value: user.display_name ?? "\u2014" },
    { label: "Email", value: user.email },
    { label: "Account ID", value: user.id },
    { label: "Role", value: user.is_superuser ? "Admin" : "User" },
    {
      label: "Email verified",
      value: user.is_verified ? (
        "Yes"
      ) : showVerifyButton ? (
        <div className="flex flex-col items-end gap-1">
          <Button size="sm" onClick={handleRequestVerify} disabled={verifyRequesting}>
            {verifyRequesting ? "Sending..." : "Verify my account"}
          </Button>
          {verifyMessage && (
            <span className="text-xs text-muted-foreground">{verifyMessage}</span>
          )}
        </div>
      ) : verifySent && verifyMessage ? (
        <span className="text-xs text-muted-foreground">{verifyMessage}</span>
      ) : (
        "No"
      ),
    },
    { label: "Member since", value: new Date(user.created_at).toLocaleDateString() },
  ];

  return (
    <div className="max-w-lg mx-auto px-6 py-10 flex flex-col gap-8">
      <div>
        <h1 className="text-xl font-semibold mb-4">Profile</h1>
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          {rows.map((row, i) => (
            <div
              key={row.label}
              className={`flex items-center justify-between px-5 py-4 ${i < rows.length - 1 ? "border-b border-border" : ""}`}
            >
              <span className="text-sm text-muted-foreground">{row.label}</span>
              <span className="text-sm font-medium text-right break-all max-w-[60%]">{row.value}</span>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-base font-semibold mb-4">OpenRouter API Key</h2>
        <div className="rounded-xl border border-border bg-card p-5 space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">Status</span>
            <span className={`text-sm font-medium ${hasKey ? "text-green-600" : "text-amber-600"}`}>
              {hasKey ? "Configured" : "Not set"}
            </span>
          </div>

          {!user.is_superuser && !hasKey && (
            <p className="text-xs text-amber-600 bg-amber-50 dark:bg-amber-950/30 rounded-lg px-3 py-2">
              An API key is required to use research features. Get one from OpenRouter.
            </p>
          )}

          {showForm ? (
            <div className="space-y-3">
              <Input
                type="password"
                placeholder="sk-or-..."
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSaveKey()}
              />
              <div className="flex gap-2">
                <Button size="sm" onClick={handleSaveKey} disabled={saving || !apiKey.trim()}>
                  {saving ? "Saving..." : "Save key"}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => { setShowForm(false); setApiKey(""); }}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => setShowForm(true)}>
                {hasKey ? "Change key" : "Add key"}
              </Button>
              {hasKey && (
                <Button size="sm" variant="ghost" onClick={handleRemoveKey} disabled={removing}>
                  {removing ? "Removing..." : "Remove"}
                </Button>
              )}
            </div>
          )}

          <a
            href="https://openrouter.ai/keys"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            Get an OpenRouter API key <ExternalLink className="size-3" />
          </a>

          {message && (
            <p className="text-xs text-muted-foreground">{message}</p>
          )}
        </div>
      </div>

      <div>
        <h2 className="text-base font-semibold mb-4">API access</h2>
        <Link
          href="/profile/tokens"
          className="flex items-center gap-3 rounded-xl border border-border bg-card px-5 py-4 hover:bg-accent/50 transition-colors"
        >
          <KeyRound className="size-5 text-muted-foreground shrink-0" />
          <div>
            <p className="text-sm font-medium">API tokens</p>
            <p className="text-xs text-muted-foreground">Generate long-lived tokens for API and MCP access</p>
          </div>
        </Link>
      </div>
    </div>
  );
}
