"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  ArrowLeftRight,
  ChevronDown,
  ChevronRight,
  Loader2,
  Plus,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { NodeResponse } from "@/types";
import type { PerspectivePair } from "@/hooks/useNodeDetail";

interface PerspectivesTabProps {
  pairs: PerspectivePair[];
  parentNode: NodeResponse;
  onNodeSelect?: (nodeId: string) => void;
  onCreated?: () => void;
}

export function PerspectivesTab({
  pairs,
  parentNode,
  onNodeSelect,
  onCreated,
}: PerspectivesTabProps) {
  const [expandedPairs, setExpandedPairs] = useState<Set<number>>(() => {
    // If parent is disabled (no definition yet or 0 richness), collapse by default
    const parentDisabled =
      !parentNode.definition && parentNode.richness === 0;
    return parentDisabled ? new Set<number>() : new Set(pairs.map((_, i) => i));
  });
  const [createDialogOpen, setCreateDialogOpen] = useState(false);

  const togglePair = (index: number) => {
    setExpandedPairs((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  if (pairs.length === 0 && !createDialogOpen) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-muted-foreground">
          No perspective pairs yet for this concept.
        </p>
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          onClick={() => setCreateDialogOpen(true)}
        >
          <Plus className="h-3.5 w-3.5" />
          Create Perspective Pair
        </Button>
        {createDialogOpen && (
          <CreatePerspectiveDialog
            parentConcept={parentNode.concept}
            onClose={() => setCreateDialogOpen(false)}
            onCreated={onCreated}
          />
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {pairs.map((pair, index) => (
        <div
          key={`${pair.thesis.id}-${pair.antithesis.id}`}
          className="rounded-md border"
        >
          <button
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium hover:bg-muted/50 transition-colors"
            onClick={() => togglePair(index)}
          >
            {expandedPairs.has(index) ? (
              <ChevronDown className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
            )}
            <ArrowLeftRight className="h-3.5 w-3.5 flex-shrink-0 text-amber-500" />
            <span className="truncate">
              {pair.thesis.concept}
            </span>
            <span className="text-muted-foreground flex-shrink-0">vs</span>
            <span className="truncate">
              {pair.antithesis.concept}
            </span>
          </button>

          {expandedPairs.has(index) && (
            <div className="border-t px-3 py-2 space-y-2">
              <PerspectiveCard
                node={pair.thesis}
                role="thesis"
                onSelect={onNodeSelect}
              />
              <PerspectiveCard
                node={pair.antithesis}
                role="antithesis"
                onSelect={onNodeSelect}
              />
            </div>
          )}
        </div>
      ))}

      <Button
        variant="outline"
        size="sm"
        className="w-full gap-1.5"
        onClick={() => setCreateDialogOpen(true)}
      >
        <Plus className="h-3.5 w-3.5" />
        Create Perspective Pair
      </Button>

      {createDialogOpen && (
        <CreatePerspectiveDialog
          parentConcept={parentNode.concept}
          onClose={() => setCreateDialogOpen(false)}
          onCreated={onCreated}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PerspectiveCard — one side of a thesis/antithesis pair
// ---------------------------------------------------------------------------

interface PerspectiveCardProps {
  node: NodeResponse;
  role: "thesis" | "antithesis";
  onSelect?: (nodeId: string) => void;
}

function PerspectiveCard({ node, role, onSelect }: PerspectiveCardProps) {
  const isThesis = role === "thesis";

  return (
    <button
      className={cn(
        "w-full text-left rounded-md border px-3 py-2 text-sm transition-colors hover:bg-muted/50",
        isThesis
          ? "border-blue-200 dark:border-blue-800"
          : "border-red-200 dark:border-red-800"
      )}
      onClick={() => onSelect?.(node.id)}
    >
      <div className="flex items-center gap-2 mb-1">
        <Badge
          className={cn(
            "text-[10px] px-1.5 py-0",
            isThesis
              ? "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200"
              : "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200"
          )}
        >
          {isThesis ? "Thesis" : "Antithesis"}
        </Badge>
        <span className="text-xs text-muted-foreground">
          Richness: {node.richness?.toFixed(2) ?? "0.00"}
        </span>
      </div>
      <p className="font-medium">{node.concept}</p>
      {node.definition && (
        <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
          {node.definition}
        </p>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// CreatePerspectiveDialog — create a new thesis/antithesis pair
// ---------------------------------------------------------------------------

interface CreatePerspectiveDialogProps {
  parentConcept: string;
  onClose: () => void;
  onCreated?: () => void;
}

function CreatePerspectiveDialog({
  parentConcept,
  onClose,
  onCreated,
}: CreatePerspectiveDialogProps) {
  const [thesis, setThesis] = useState("");
  const [antithesis, setAntithesis] = useState("");
  const [isValidating, setIsValidating] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [validation, setValidation] = useState<{
    valid: boolean;
    feedback: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const canValidate = thesis.trim().length > 0 && antithesis.trim().length > 0;

  const handleValidate = async () => {
    if (!canValidate) return;
    setIsValidating(true);
    setError(null);
    setValidation(null);

    try {
      const result = await api.nodes.quickPerspectiveValidate({
        thesis: thesis.trim(),
        antithesis: antithesis.trim(),
        parent_concept: parentConcept,
      });
      setValidation(result);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Validation failed"
      );
    } finally {
      setIsValidating(false);
    }
  };

  const handleCreate = async () => {
    if (!canValidate) return;
    setIsCreating(true);
    setError(null);

    try {
      const result = await api.nodes.quickPerspective({
        thesis: thesis.trim(),
        antithesis: antithesis.trim(),
        parent_concept: parentConcept,
      });

      if (result.thesis_id && result.antithesis_id) {
        onCreated?.();
        onClose();
      } else {
        setError("Creation failed — check validation feedback.");
        if (result.validation) {
          setValidation(result.validation);
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Creation failed");
    } finally {
      setIsCreating(false);
    }
  };

  const busy = isValidating || isCreating;

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>New Perspective Pair</DialogTitle>
          <DialogDescription>
            Create a thesis/antithesis pair for{" "}
            <span className="font-medium">{parentConcept}</span>.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="thesis-input">Thesis</Label>
            <Input
              id="thesis-input"
              placeholder="e.g. Climate change requires immediate action"
              value={thesis}
              onChange={(e) => {
                setThesis(e.target.value);
                setValidation(null);
              }}
              disabled={busy}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="antithesis-input">Antithesis</Label>
            <Input
              id="antithesis-input"
              placeholder="e.g. Climate change is not an urgent priority"
              value={antithesis}
              onChange={(e) => {
                setAntithesis(e.target.value);
                setValidation(null);
              }}
              disabled={busy}
            />
          </div>

          {validation && (
            <div
              className={cn(
                "rounded-md border px-3 py-2 text-sm",
                validation.valid
                  ? "border-green-200 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-200"
                  : "border-red-200 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
              )}
            >
              <p className="font-medium mb-0.5">
                {validation.valid ? "Valid pair" : "Invalid pair"}
              </p>
              <p className="text-xs">{validation.feedback}</p>
            </div>
          )}

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}
        </div>

        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="secondary"
            onClick={handleValidate}
            disabled={!canValidate || busy}
          >
            {isValidating && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            Validate
          </Button>
          <Button
            onClick={handleCreate}
            disabled={!canValidate || busy}
          >
            {isCreating && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
