"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import type { BottomUpProposedNode } from "@/types";

interface AgentSelectDialogProps {
  conversationId: string;
  totalNodes: number;
  /** Default context (query or document name) shown as placeholder. */
  defaultContext: string;
  /** Called when agent selection completes — parent should refresh nodes. */
  onComplete: (updatedNodes: BottomUpProposedNode[]) => void;
  /** Which proposals endpoint to use. */
  mode: "bottom-up" | "ingest";
}

export function AgentSelectDialog({
  conversationId,
  totalNodes,
  defaultContext,
  onComplete,
  mode,
}: AgentSelectDialogProps) {
  const [open, setOpen] = useState(false);
  const [maxSelect, setMaxSelect] = useState(Math.min(20, totalNodes));
  const [instructions, setInstructions] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  const handleSubmit = useCallback(async () => {
    setIsRunning(true);
    setError(null);

    try {
      await api.research.agentSelect(
        conversationId,
        maxSelect,
        instructions || undefined,
      );

      // Poll the proposals endpoint — it now includes agent_select_status
      pollRef.current = setInterval(async () => {
        try {
          if (mode === "bottom-up") {
            const proposals =
              await api.research.bottomUpProposals(conversationId);
            if (proposals.agent_select_status === "completed") {
              stopPolling();
              setIsRunning(false);
              setOpen(false);
              onComplete(proposals.proposed_nodes);
            }
          } else {
            const proposals = await api.research.proposals(conversationId);
            if (proposals.agent_select_status === "completed") {
              stopPolling();
              setIsRunning(false);
              setOpen(false);
              onComplete(proposals.proposed_nodes);
            }
          }
        } catch {
          // Keep polling on transient errors
        }
      }, 3000);

      // Safety timeout after 5 minutes
      timeoutRef.current = setTimeout(() => {
        if (pollRef.current) {
          stopPolling();
          setIsRunning(false);
          setError(
            "Agent selection timed out. You can try again or select manually.",
          );
        }
      }, 300_000);
    } catch (err) {
      setIsRunning(false);
      setError(
        err instanceof Error ? err.message : "Failed to start agent selection",
      );
    }
  }, [conversationId, maxSelect, instructions, mode, onComplete, stopPolling]);

  const handleCancel = useCallback(() => {
    stopPolling();
    setIsRunning(false);
    setOpen(false);
  }, [stopPolling]);

  return (
    <Dialog open={open} onOpenChange={(v) => !isRunning && setOpen(v)}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="text-xs h-7 gap-1.5">
          <Sparkles className="size-3" />
          Help me choose
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="size-4" />
            Agent-Assisted Selection
          </DialogTitle>
          <DialogDescription>
            An AI agent will review {totalNodes} proposed nodes and select the
            most relevant ones, skipping duplicates and generic entries.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div>
            <Label className="text-sm">How many nodes to select</Label>
            <Input
              type="number"
              min={1}
              max={totalNodes}
              value={maxSelect}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                setMaxSelect(
                  Math.max(
                    1,
                    Math.min(totalNodes, Number(e.target.value) || 1),
                  ),
                )
              }
              className="mt-1.5"
              disabled={isRunning}
            />
            <p className="text-xs text-muted-foreground mt-1">
              The agent will select up to this many nodes from {totalNodes}{" "}
              candidates.
            </p>
          </div>

          <div>
            <Label className="text-sm">
              Instructions{" "}
              <span className="text-muted-foreground font-normal">
                (optional)
              </span>
            </Label>
            <Textarea
              placeholder={defaultContext || "Any specific focus or preferences?"}
              value={instructions}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                setInstructions(e.target.value)
              }
              rows={3}
              className="mt-1.5"
              disabled={isRunning}
            />
            <p className="text-xs text-muted-foreground mt-1">
              Leave empty to use the original query as context.
            </p>
          </div>

          {error && (
            <div className="text-sm text-red-500 bg-red-50 dark:bg-red-950/20 rounded px-3 py-2">
              {error}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleCancel}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={isRunning}>
            {isRunning ? (
              <>
                <Loader2 className="size-4 mr-2 animate-spin" />
                Selecting...
              </>
            ) : (
              <>
                <Sparkles className="size-4 mr-2" />
                Let the agent choose
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
