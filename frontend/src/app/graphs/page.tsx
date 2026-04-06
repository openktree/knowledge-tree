"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Plus, Search, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/contexts/auth";
import { useGraph } from "@/contexts/graph";
import { listGraphs, createGraph, listDatabaseConnections } from "@/lib/api";
import { DeleteGraphDialog } from "@/components/graphs/DeleteGraphDialog";
import type { GraphResponse, DatabaseConnectionResponse } from "@/types";

export default function GraphsPage() {
  const { user } = useAuth();
  const { refreshGraphs: refreshGraphContext } = useGraph();
  const [graphs, setGraphs] = useState<GraphResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [dbFilter, setDbFilter] = useState<string>("all");
  const [deletingGraph, setDeletingGraph] = useState<GraphResponse | null>(null);

  // Create form state
  const [newSlug, setNewSlug] = useState("");
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newStorageMode, setNewStorageMode] = useState("schema");
  const [newDbConnKey, setNewDbConnKey] = useState("");
  const [dbConnections, setDbConnections] = useState<DatabaseConnectionResponse[]>([]);

  const fetchGraphs = useCallback(async () => {
    try {
      const data = await listGraphs();
      setGraphs(data);
    } catch (err) {
      console.error("Failed to load graphs:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGraphs();
  }, [fetchGraphs]);

  // Fetch DB connections when create form opens (superusers only)
  useEffect(() => {
    if (showCreate && user?.is_superuser && dbConnections.length === 0) {
      listDatabaseConnections()
        .then(setDbConnections)
        .catch(() => {});
    }
  }, [showCreate, user?.is_superuser, dbConnections.length]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      await createGraph({
        slug: newSlug,
        name: newName,
        description: newDescription || undefined,
        storage_mode: newStorageMode,
        database_connection_config_key:
          newStorageMode === "database" && newDbConnKey ? newDbConnKey : undefined,
      });
      setNewSlug("");
      setNewName("");
      setNewDescription("");
      setNewStorageMode("schema");
      setNewDbConnKey("");
      setShowCreate(false);
      fetchGraphs();
      refreshGraphContext();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to create graph");
    } finally {
      setCreating(false);
    }
  };

  const handleDeleted = () => {
    setDeletingGraph(null);
    fetchGraphs();
    refreshGraphContext();
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

  // Filter graphs
  const filteredGraphs = graphs.filter((g) => {
    const matchesSearch =
      !searchQuery ||
      g.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      g.slug.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (g.description ?? "").toLowerCase().includes(searchQuery.toLowerCase());
    const matchesDb =
      dbFilter === "all" ||
      (dbFilter === "schema" && !g.database_connection_id) ||
      g.database_connection_id === dbFilter;
    return matchesSearch && matchesDb;
  });

  // Unique DB connections from graphs for filter dropdown
  const uniqueDbConnections = Array.from(
    new Map(
      graphs
        .filter((g) => g.database_connection_id && g.database_connection_name)
        .map((g) => [g.database_connection_id!, g.database_connection_name!]),
    ).entries(),
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const nonDefaultGraphs = graphs.filter((g) => !g.is_default);

  return (
    <div className="max-w-5xl mx-auto px-6 py-10">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold">Graphs</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {graphs.length} graph{graphs.length !== 1 ? "s" : ""}
          </p>
        </div>
        {user?.is_superuser && !showCreate && (
          <Button size="sm" onClick={() => setShowCreate(true)}>
            <Plus className="mr-1.5 size-3.5" />
            Create Graph
          </Button>
        )}
      </div>

      {/* Create form */}
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
                pattern="[a-z0-9][a-z0-9_]{1,98}[a-z0-9]"
                value={newSlug}
                onChange={(e) => setNewSlug(e.target.value)}
                placeholder="my_research"
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
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <label htmlFor="g-mode" className="text-xs font-medium text-muted-foreground">
                Storage Mode
              </label>
              <select
                id="g-mode"
                value={newStorageMode}
                onChange={(e) => {
                  setNewStorageMode(e.target.value);
                  if (e.target.value === "schema") setNewDbConnKey("");
                }}
                className="rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="schema">Schema (same DB, separate schema)</option>
                <option value="database">Database (different DB)</option>
              </select>
            </div>
            {newStorageMode === "database" && (
              <div className="flex flex-col gap-1">
                <label htmlFor="g-db" className="text-xs font-medium text-muted-foreground">
                  Database Connection
                </label>
                <select
                  id="g-db"
                  required
                  value={newDbConnKey}
                  onChange={(e) => setNewDbConnKey(e.target.value)}
                  className="rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <option value="">Select a connection...</option>
                  {dbConnections.map((c) => (
                    <option key={c.config_key} value={c.config_key}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
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

      {/* Search and filter */}
      {graphs.length > 1 && (
        <div className="flex gap-3 mb-4">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search by name or slug..."
              className="pl-8 h-8 text-sm"
            />
          </div>
          {uniqueDbConnections.length > 0 && (
            <select
              value={dbFilter}
              onChange={(e) => setDbFilter(e.target.value)}
              className="rounded-md border border-border bg-background px-3 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="all">All databases</option>
              <option value="schema">Default (schema mode)</option>
              {uniqueDbConnections.map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* Graph cards */}
      {filteredGraphs.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filteredGraphs.map((g) => (
            <Link
              key={g.id}
              href={`/graphs/${g.slug}`}
              className="rounded-xl border border-border bg-card p-4 hover:border-ring transition-colors block relative group"
            >
              {/* Delete button */}
              {user?.is_superuser && !g.is_default && (
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setDeletingGraph(g);
                  }}
                  className="absolute top-3 right-3 opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive"
                  title="Delete graph"
                >
                  <Trash2 className="size-3.5" />
                </button>
              )}

              <div className="flex items-start justify-between mb-2 pr-6">
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
                <span className="text-[10px]">
                  {new Date(g.created_at).toLocaleDateString(undefined, {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                  })}
                </span>
              </div>
              <div className="flex gap-2 mt-2 text-[10px] text-muted-foreground">
                <span>
                  {g.database_connection_name
                    ? g.database_connection_name
                    : g.storage_mode === "database"
                      ? "Separate DB"
                      : "Shared DB"}
                </span>
                <span>Type: {g.graph_type}</span>
                {g.byok_enabled && (
                  <Badge variant="outline" className="text-[10px] h-4">
                    BYOK
                  </Badge>
                )}
              </div>
            </Link>
          ))}
        </div>
      ) : searchQuery || dbFilter !== "all" ? (
        <div className="text-center py-10">
          <p className="text-sm text-muted-foreground">
            No graphs matching your filters.
          </p>
          <Button
            variant="ghost"
            size="sm"
            className="mt-2"
            onClick={() => {
              setSearchQuery("");
              setDbFilter("all");
            }}
          >
            Clear filters
          </Button>
        </div>
      ) : nonDefaultGraphs.length === 0 ? (
        <div className="text-center py-10">
          <p className="text-sm text-muted-foreground mb-1">
            No custom graphs yet.
          </p>
          <p className="text-xs text-muted-foreground mb-4">
            All data is stored in the default graph. Create a new graph to
            organize research separately.
          </p>
          {user?.is_superuser && (
            <Button size="sm" onClick={() => setShowCreate(true)}>
              <Plus className="mr-1.5 size-3.5" />
              Create your first graph
            </Button>
          )}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground text-center py-10">
          No graphs found.
        </p>
      )}

      <DeleteGraphDialog
        graph={deletingGraph}
        open={deletingGraph !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingGraph(null);
        }}
        onDeleted={handleDeleted}
      />
    </div>
  );
}
