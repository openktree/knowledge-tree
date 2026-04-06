"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Copy, Plus, Trash2 } from "lucide-react";
import { api, listGraphs } from "@/lib/api";
import type { ApiTokenCreated, ApiTokenRead, GraphResponse } from "@/types";

// ---------------------------------------------------------------------------
// New token banner
// ---------------------------------------------------------------------------

function NewTokenBanner({ token, onDismiss }: { token: ApiTokenCreated; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);

  function copy() {
    navigator.clipboard.writeText(token.token);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="rounded-lg border border-green-500/40 bg-green-500/10 p-4 text-sm">
      <p className="font-medium text-green-400 mb-1">
        Token created — copy it now. It won&apos;t be shown again.
      </p>
      <div className="flex items-center gap-2 mt-2">
        <code className="flex-1 truncate rounded bg-black/30 px-3 py-1.5 text-xs font-mono text-green-300">
          {token.token}
        </code>
        <button
          onClick={copy}
          className="shrink-0 rounded-md border border-border px-3 py-1.5 text-xs hover:bg-accent"
        >
          {copied ? "Copied!" : <Copy className="size-3.5" />}
        </button>
      </div>
      <button
        onClick={onDismiss}
        className="mt-3 text-xs text-muted-foreground underline hover:text-foreground"
      >
        Dismiss
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Expiry picker
// ---------------------------------------------------------------------------

type ExpiryMode = "none" | "30" | "90" | "365" | "custom";

const PRESETS: { label: string; value: ExpiryMode }[] = [
  { label: "No expiry", value: "none" },
  { label: "30 days", value: "30" },
  { label: "90 days", value: "90" },
  { label: "1 year", value: "365" },
  { label: "Custom", value: "custom" },
];

function expiryIsoFromMode(mode: ExpiryMode, customDate: string): string | undefined {
  if (mode === "none") return undefined;
  if (mode === "custom") return customDate ? new Date(customDate).toISOString() : undefined;
  const d = new Date();
  d.setDate(d.getDate() + Number(mode));
  return d.toISOString();
}

// Min date for the custom date input (tomorrow)
function minDate(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

interface ExpiryPickerProps {
  mode: ExpiryMode;
  customDate: string;
  onModeChange: (m: ExpiryMode) => void;
  onCustomDateChange: (v: string) => void;
}

function ExpiryPicker({ mode, customDate, onModeChange, onCustomDateChange }: ExpiryPickerProps) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2 flex-wrap">
        {PRESETS.map((p) => (
          <button
            key={p.value}
            type="button"
            onClick={() => onModeChange(p.value)}
            className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
              mode === p.value
                ? "border-primary bg-primary/10 text-primary"
                : "border-border hover:bg-accent"
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>
      {mode === "custom" && (
        <input
          type="date"
          min={minDate()}
          value={customDate}
          onChange={(e) => onCustomDateChange(e.target.value)}
          required
          className="w-44 rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create form
// ---------------------------------------------------------------------------

function CreateTokenForm({ onCreated }: { onCreated: (t: ApiTokenCreated) => void }) {
  const [name, setName] = useState("");
  const [mode, setMode] = useState<ExpiryMode>("none");
  const [customDate, setCustomDate] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [graphs, setGraphs] = useState<GraphResponse[]>([]);
  const [selectedGraphs, setSelectedGraphs] = useState<string[]>([]);
  const [restrictGraphs, setRestrictGraphs] = useState(false);

  useEffect(() => {
    listGraphs().then(setGraphs).catch((err) => console.error("Failed to load graphs:", err));
  }, []);

  function toggleGraph(slug: string) {
    setSelectedGraphs((prev) =>
      prev.includes(slug) ? prev.filter((s) => s !== slug) : [...prev, slug]
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    if (mode === "custom" && !customDate) return;
    setError(null);
    setLoading(true);
    try {
      const expiresAt = expiryIsoFromMode(mode, customDate);
      const graphSlugs = restrictGraphs && selectedGraphs.length > 0 ? selectedGraphs : undefined;
      const created = await api.auth.createToken(name.trim(), expiresAt, graphSlugs);
      onCreated(created);
      setName("");
      setMode("none");
      setCustomDate("");
      setRestrictGraphs(false);
      setSelectedGraphs([]);
    } catch {
      setError("Failed to create token.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-xl border border-border bg-card p-5">
      <h3 className="text-sm font-semibold mb-4">Generate new token</h3>
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-muted-foreground">Token name</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. MCP client, CI script"
            required
            className="rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="text-xs text-muted-foreground">Expiration</label>
          <ExpiryPicker
            mode={mode}
            customDate={customDate}
            onModeChange={setMode}
            onCustomDateChange={setCustomDate}
          />
        </div>

        {graphs.length > 1 && (
          <div className="flex flex-col gap-1.5">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={restrictGraphs}
                onChange={(e) => setRestrictGraphs(e.target.checked)}
                className="rounded"
              />
              Restrict to specific graphs
            </label>
            {restrictGraphs && (
              <div className="flex gap-2 flex-wrap ml-5">
                {graphs.map((g) => (
                  <button
                    key={g.slug}
                    type="button"
                    onClick={() => toggleGraph(g.slug)}
                    className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
                      selectedGraphs.includes(g.slug)
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border hover:bg-accent"
                    }`}
                  >
                    {g.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {error && <p className="text-xs text-red-500">{error}</p>}

        <button
          type="submit"
          disabled={loading || !name.trim() || (mode === "custom" && !customDate)}
          className="flex items-center gap-2 self-start rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          <Plus className="size-4" />
          {loading ? "Creating…" : "Create token"}
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Token list
// ---------------------------------------------------------------------------

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function TokenList({ tokens, onRevoke }: { tokens: ApiTokenRead[]; onRevoke: (id: string) => void }) {
  if (tokens.length === 0) {
    return (
      <p className="text-sm text-muted-foreground px-1">
        No API tokens yet. Generate one above.
      </p>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      {tokens.map((token, i) => {
        const expired = token.expires_at && new Date(token.expires_at) < new Date();
        return (
          <div
            key={token.id}
            className={`flex items-center justify-between px-5 py-3.5 ${
              i < tokens.length - 1 ? "border-b border-border" : ""
            }`}
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium truncate">{token.name}</p>
                {expired && (
                  <span className="rounded-full bg-destructive/15 px-2 py-0.5 text-[10px] font-medium text-destructive">
                    expired
                  </span>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                Created {formatDate(token.created_at)}
                {token.expires_at && ` · Expires ${formatDate(token.expires_at)}`}
                {token.last_used_at && ` · Last used ${formatDate(token.last_used_at)}`}
                {token.graph_slugs && ` · Graphs: ${token.graph_slugs.join(", ")}`}
                {!token.graph_slugs && " · All graphs"}
              </p>
            </div>
            <button
              onClick={() => onRevoke(token.id)}
              className="ml-4 shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
              title="Revoke token"
            >
              <Trash2 className="size-4" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function TokensPage() {
  const [tokens, setTokens] = useState<ApiTokenRead[]>([]);
  const [newToken, setNewToken] = useState<ApiTokenCreated | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.auth.listTokens()
      .then((list) => { if (!cancelled) setTokens(list); })
      .catch((err: unknown) => console.error("Failed to load tokens:", err));
    return () => { cancelled = true; };
  }, []);

  async function handleRevoke(id: string) {
    try {
      await api.auth.revokeToken(id);
      setTokens((prev) => prev.filter((t) => t.id !== id));
    } catch { /* ignore */ }
  }

  function handleCreated(token: ApiTokenCreated) {
    setNewToken(token);
    setTokens((prev) => [
      { id: token.id, name: token.name, created_at: token.created_at, expires_at: token.expires_at, last_used_at: null, graph_slugs: token.graph_slugs },
      ...prev,
    ]);
  }

  return (
    <div className="max-w-lg mx-auto px-6 py-10 flex flex-col gap-6">
      <div className="flex items-start gap-3">
        <Link
          href="/profile"
          className="rounded-md p-1.5 mt-0.5 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <div>
          <h1 className="text-xl font-semibold">API Tokens</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Generate tokens to authenticate API requests or connect MCP clients
            (such as Claude Desktop) to your knowledge graph.
          </p>
        </div>
      </div>

      {newToken && (
        <NewTokenBanner token={newToken} onDismiss={() => setNewToken(null)} />
      )}

      <CreateTokenForm onCreated={handleCreated} />
      <TokenList tokens={tokens} onRevoke={handleRevoke} />
    </div>
  );
}
