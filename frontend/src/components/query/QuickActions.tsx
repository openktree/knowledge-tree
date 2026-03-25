"use client";

import { useState, useCallback } from "react";
import {
  Plus,
  RefreshCw,
  GitCompareArrows,
  Loader2,
  CheckCircle,
  XCircle,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import type {
  QuickAddNodeResponse,
  QuickPerspectiveResponse,
  QuickPerspectiveValidateResponse,
  NodeResponse,
} from "@/types";

interface QuickActionsProps {
  onNodeCreated?: (nodeId: string) => void;
}

export function QuickActions({ onNodeCreated }: QuickActionsProps) {
  const [expanded, setExpanded] = useState<
    "add-node" | "add-perspective" | "refresh" | null
  >(null);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Zap className="size-3.5" />
        <span className="font-medium">Quick Actions</span>
        <Badge variant="outline" className="text-[10px] px-1.5 py-0">
          1 nav credit each
        </Badge>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <Button
          variant={expanded === "add-node" ? "default" : "outline"}
          size="sm"
          className="h-8 text-xs gap-1.5"
          onClick={() =>
            setExpanded(expanded === "add-node" ? null : "add-node")
          }
        >
          <Plus className="size-3.5" />
          Add Node
        </Button>
        <Button
          variant={expanded === "add-perspective" ? "default" : "outline"}
          size="sm"
          className="h-8 text-xs gap-1.5"
          onClick={() =>
            setExpanded(
              expanded === "add-perspective" ? null : "add-perspective",
            )
          }
        >
          <GitCompareArrows className="size-3.5" />
          Add Perspective
        </Button>
        <Button
          variant={expanded === "refresh" ? "default" : "outline"}
          size="sm"
          className="h-8 text-xs gap-1.5"
          onClick={() =>
            setExpanded(expanded === "refresh" ? null : "refresh")
          }
        >
          <RefreshCw className="size-3.5" />
          Refresh Node
        </Button>
      </div>

      {expanded === "add-node" && (
        <AddNodePanel onNodeCreated={onNodeCreated} />
      )}
      {expanded === "add-perspective" && (
        <AddPerspectivePanel onNodeCreated={onNodeCreated} />
      )}
      {expanded === "refresh" && (
        <RefreshNodePanel />
      )}
    </div>
  );
}

// ── Add Node Panel ──────────────────────────────────────────────────────

function AddNodePanel({
  onNodeCreated,
}: {
  onNodeCreated?: (nodeId: string) => void;
}) {
  const [concept, setConcept] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [result, setResult] = useState<QuickAddNodeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(async () => {
    if (!concept.trim()) return;
    setIsSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.nodes.quickAdd({ concept: concept.trim() });
      setResult(res);
      onNodeCreated?.(res.node_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add node");
    } finally {
      setIsSubmitting(false);
    }
  }, [concept, onNodeCreated]);

  return (
    <Card>
      <CardContent className="pt-4 space-y-3">
        <p className="text-xs text-muted-foreground">
          Create a new concept node. If the node already exists, it will be
          refreshed instead.
        </p>
        <div className="flex gap-2">
          <Input
            placeholder="Concept name, e.g. 'Quantum Computing'"
            value={concept}
            onChange={(e) => setConcept(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSubmit();
            }}
            disabled={isSubmitting}
            className="h-8 text-sm"
          />
          <Button
            size="sm"
            className="h-8"
            disabled={isSubmitting || !concept.trim()}
            onClick={handleSubmit}
          >
            {isSubmitting ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Plus className="size-3.5" />
            )}
          </Button>
        </div>
        {result && (
          <div className="flex items-center gap-2 text-xs text-green-600">
            <CheckCircle className="size-3.5" />
            Node {result.action === "created" ? "created" : "refresh started"}:{" "}
            <a
              href={`/nodes/${result.node_id}`}
              className="underline font-medium"
            >
              {result.concept}
            </a>
          </div>
        )}
        {error && (
          <div className="flex items-center gap-2 text-xs text-destructive">
            <XCircle className="size-3.5" />
            {error}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Add Perspective Panel ───────────────────────────────────────────────

function AddPerspectivePanel({
  onNodeCreated,
}: {
  onNodeCreated?: (nodeId: string) => void;
}) {
  const [thesis, setThesis] = useState("");
  const [antithesis, setAntithesis] = useState("");
  const [parentConcept, setParentConcept] = useState("");
  const [isValidating, setIsValidating] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [validation, setValidation] =
    useState<QuickPerspectiveValidateResponse | null>(null);
  const [result, setResult] = useState<QuickPerspectiveResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleValidate = useCallback(async () => {
    if (!thesis.trim() || !antithesis.trim()) return;
    setIsValidating(true);
    setValidation(null);
    setError(null);
    try {
      const res = await api.nodes.quickPerspectiveValidate({
        thesis: thesis.trim(),
        antithesis: antithesis.trim(),
        parent_concept: parentConcept.trim() || null,
      });
      setValidation(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Validation failed");
    } finally {
      setIsValidating(false);
    }
  }, [thesis, antithesis, parentConcept]);

  const handleSubmit = useCallback(async () => {
    if (!thesis.trim() || !antithesis.trim()) return;
    setIsSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.nodes.quickPerspective({
        thesis: thesis.trim(),
        antithesis: antithesis.trim(),
        parent_concept: parentConcept.trim() || null,
      });
      if (res.status === "rejected") {
        setValidation(res.validation);
        setError("Perspective pair rejected by validation");
      } else {
        setResult(res);
        if (res.thesis_id) onNodeCreated?.(res.thesis_id);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create perspective");
    } finally {
      setIsSubmitting(false);
    }
  }, [thesis, antithesis, parentConcept, onNodeCreated]);

  const canValidate = thesis.trim().length > 0 && antithesis.trim().length > 0;
  const canSubmit = canValidate && validation?.valid === true;

  return (
    <Card>
      <CardContent className="pt-4 space-y-3">
        <p className="text-xs text-muted-foreground">
          Create a thesis/antithesis perspective pair. The antithesis will be
          validated by AI before creation.
        </p>
        <Input
          placeholder="Thesis — e.g. 'AI will create more jobs than it destroys'"
          value={thesis}
          onChange={(e) => {
            setThesis(e.target.value);
            setValidation(null);
            setResult(null);
          }}
          disabled={isSubmitting}
          className="h-8 text-sm"
        />
        <Input
          placeholder="Antithesis — e.g. 'AI will cause net job losses'"
          value={antithesis}
          onChange={(e) => {
            setAntithesis(e.target.value);
            setValidation(null);
            setResult(null);
          }}
          disabled={isSubmitting}
          className="h-8 text-sm"
        />
        <Input
          placeholder="Parent concept (optional) — e.g. 'Artificial Intelligence'"
          value={parentConcept}
          onChange={(e) => setParentConcept(e.target.value)}
          disabled={isSubmitting}
          className="h-8 text-sm"
        />

        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs"
            disabled={!canValidate || isValidating || isSubmitting}
            onClick={handleValidate}
          >
            {isValidating ? (
              <Loader2 className="size-3.5 animate-spin mr-1" />
            ) : null}
            Validate
          </Button>
          <Button
            size="sm"
            className="h-8 text-xs"
            disabled={!canSubmit || isSubmitting}
            onClick={handleSubmit}
          >
            {isSubmitting ? (
              <Loader2 className="size-3.5 animate-spin mr-1" />
            ) : null}
            Create Pair
          </Button>
        </div>

        {validation && (
          <div
            className={`flex items-start gap-2 text-xs ${validation.valid ? "text-green-600" : "text-amber-600"}`}
          >
            {validation.valid ? (
              <CheckCircle className="size-3.5 mt-0.5 shrink-0" />
            ) : (
              <XCircle className="size-3.5 mt-0.5 shrink-0" />
            )}
            <span>{validation.feedback}</span>
          </div>
        )}

        {result && result.status === "created" && (
          <div className="flex items-center gap-2 text-xs text-green-600">
            <CheckCircle className="size-3.5" />
            <span>
              Perspective pair created:{" "}
              <a
                href={`/nodes/${result.thesis_id}`}
                className="underline font-medium"
              >
                thesis
              </a>
              {" / "}
              <a
                href={`/nodes/${result.antithesis_id}`}
                className="underline font-medium"
              >
                antithesis
              </a>
            </span>
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 text-xs text-destructive">
            <XCircle className="size-3.5" />
            {error}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Refresh Node Panel ──────────────────────────────────────────────────

function RefreshNodePanel() {
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<NodeResponse[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState<string | null>(null);
  const [refreshedId, setRefreshedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSearch = useCallback(async () => {
    if (!query.trim()) return;
    setIsSearching(true);
    setError(null);
    try {
      const results = await api.nodes.search(query.trim(), 5);
      setSearchResults(results);
      if (results.length === 0) {
        setError("No nodes found matching that query");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed");
    } finally {
      setIsSearching(false);
    }
  }, [query]);

  const handleRefresh = useCallback(async (nodeId: string) => {
    setIsRefreshing(nodeId);
    setRefreshedId(null);
    setError(null);
    try {
      await api.nodes.rebuildNode(nodeId, "full", "all");
      setRefreshedId(nodeId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Refresh failed");
    } finally {
      setIsRefreshing(null);
    }
  }, []);

  return (
    <Card>
      <CardContent className="pt-4 space-y-3">
        <p className="text-xs text-muted-foreground">
          Search for an existing node and trigger a dimension/edge
          recalculation.
        </p>
        <div className="flex gap-2">
          <Input
            placeholder="Search nodes..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSearch();
            }}
            disabled={isRefreshing !== null}
            className="h-8 text-sm"
          />
          <Button
            variant="outline"
            size="sm"
            className="h-8"
            disabled={!query.trim() || isSearching || isRefreshing !== null}
            onClick={handleSearch}
          >
            {isSearching ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              "Search"
            )}
          </Button>
        </div>

        {searchResults.length > 0 && (
          <div className="space-y-1 max-h-40 overflow-y-auto">
            {searchResults.map((node) => (
              <div
                key={node.id}
                className="flex items-center justify-between px-2 py-1.5 rounded text-xs hover:bg-muted/50"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Badge variant="outline" className="text-[10px] shrink-0">
                    {node.node_type}
                  </Badge>
                  <span className="truncate">{node.concept}</span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 text-[10px] gap-1 shrink-0"
                  disabled={isRefreshing !== null}
                  onClick={() => handleRefresh(node.id)}
                >
                  {isRefreshing === node.id ? (
                    <Loader2 className="size-3 animate-spin" />
                  ) : refreshedId === node.id ? (
                    <CheckCircle className="size-3 text-green-600" />
                  ) : (
                    <RefreshCw className="size-3" />
                  )}
                  {refreshedId === node.id ? "Started" : "Refresh"}
                </Button>
              </div>
            ))}
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 text-xs text-destructive">
            <XCircle className="size-3.5" />
            {error}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
