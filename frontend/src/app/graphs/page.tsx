"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/auth";
import { listGraphs, createGraph } from "@/lib/api";
import type { GraphResponse } from "@/types";

export default function GraphsPage() {
  const { user } = useAuth();
  const [graphs, setGraphs] = useState<GraphResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);

  // Create form state
  const [newSlug, setNewSlug] = useState("");
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newStorageMode, setNewStorageMode] = useState("schema");

  const fetchGraphs = useCallback(async () => {
    try {
      const data = await listGraphs();
      setGraphs(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGraphs();
  }, [fetchGraphs]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      await createGraph({
        slug: newSlug,
        name: newName,
        description: newDescription || undefined,
        storage_mode: newStorageMode,
      });
      setNewSlug("");
      setNewName("");
      setNewDescription("");
      setShowCreate(false);
      fetchGraphs();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create graph");
    } finally {
      setCreating(false);
    }
  };

  const statusColor = (status: string) => {
    switch (status) {
      case "active":
        return "default" as const;
      case "provisioning":
        return "secondary" as const;
      case "error":
        return "destructive" as const;
      default:
        return "secondary" as const;
    }
  };

  if (loading) {
    return (
      <div className="p-6">
        <p className="text-sm text-muted-foreground">Loading graphs...</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-6 py-10">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold">Graphs</h1>
        {user?.is_superuser && !showCreate && (
          <Button size="sm" onClick={() => setShowCreate(true)}>
            Create Graph
          </Button>
        )}
      </div>

      {showCreate && (
        <form
          onSubmit={handleCreate}
          className="mb-6 rounded-xl border border-border bg-card p-4 space-y-3"
        >
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <label htmlFor="g-slug" className="text-xs font-medium text-muted-foreground">
                Slug
              </label>
              <input
                id="g-slug"
                required
                pattern="[a-z0-9][a-z0-9_-]{1,98}[a-z0-9]"
                value={newSlug}
                onChange={(e) => setNewSlug(e.target.value)}
                placeholder="my-research"
                className="rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="g-name" className="text-xs font-medium text-muted-foreground">
                Name
              </label>
              <input
                id="g-name"
                required
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="My Research Graph"
                className="rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="g-desc" className="text-xs font-medium text-muted-foreground">
              Description (optional)
            </label>
            <input
              id="g-desc"
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
              placeholder="A private graph for..."
              className="rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor="g-mode" className="text-xs font-medium text-muted-foreground">
              Storage Mode
            </label>
            <select
              id="g-mode"
              value={newStorageMode}
              onChange={(e) => setNewStorageMode(e.target.value)}
              className="rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring w-fit"
            >
              <option value="schema">Schema (same DB, separate schema)</option>
              <option value="database">Database (different DB)</option>
            </select>
          </div>
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={creating}>
              {creating ? "Creating..." : "Create"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setShowCreate(false)}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {graphs.map((g) => (
          <a
            key={g.id}
            href={`/graphs/${g.slug}`}
            className="rounded-xl border border-border bg-card p-4 hover:border-ring transition-colors block"
          >
            <div className="flex items-start justify-between mb-2">
              <h2 className="font-medium text-sm truncate">{g.name}</h2>
              <div className="flex gap-1 ml-2 shrink-0">
                {g.is_default && (
                  <Badge variant="outline" className="text-[10px]">
                    Default
                  </Badge>
                )}
                <Badge variant={statusColor(g.status)} className="text-[10px]">
                  {g.status}
                </Badge>
              </div>
            </div>
            {g.description && (
              <p className="text-xs text-muted-foreground mb-3 line-clamp-2">
                {g.description}
              </p>
            )}
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>{g.node_count} nodes</span>
              <span>{g.member_count} members</span>
              <span className="font-mono text-[10px]">{g.schema_name}</span>
            </div>
            <div className="flex gap-2 mt-2 text-[10px] text-muted-foreground">
              <span>{g.storage_mode === "database" ? "Separate DB" : "Shared DB"}</span>
              <span>Type: {g.graph_type}</span>
              {g.byok_enabled && <Badge variant="outline" className="text-[10px] h-4">BYOK</Badge>}
            </div>
          </a>
        ))}
      </div>

      {graphs.length === 0 && (
        <p className="text-sm text-muted-foreground text-center py-10">
          No graphs found.
        </p>
      )}
    </div>
  );
}
