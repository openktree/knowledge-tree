"use client";

import { useState, useEffect, useCallback } from "react";
import { Loader2, FileText, Layers, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  createSynthesis,
  createSuperSynthesis,
  listSyntheses,
  getSynthesisModels,
} from "@/lib/api";
import { formatSynthesisConcept } from "./utils";
import { SynthesisProgress } from "./SynthesisProgress";
import type { SynthesisListItem, SynthesisModelOption } from "@/types";

type SynthesisMode = "synthesis" | "super";

interface CreateSynthesisDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}

export function CreateSynthesisDialog({
  open,
  onOpenChange,
  onCreated,
}: CreateSynthesisDialogProps) {
  const [mode, setMode] = useState<SynthesisMode>("synthesis");
  const [topic, setTopic] = useState("");
  const [budget, setBudget] = useState(20);
  const [scopeCount, setScopeCount] = useState(5);
  const [visibility, setVisibility] = useState("public");
  const [creating, setCreating] = useState(false);
  const [workflowRunId, setWorkflowRunId] = useState<string | null>(null);

  const [modelId, setModelId] = useState<string>("default");

  // Available synthesis models
  const [availableModels, setAvailableModels] = useState<SynthesisModelOption[]>([]);

  // Existing syntheses for super-synthesis inclusion
  const [existingSyntheses, setExistingSyntheses] = useState<
    SynthesisListItem[]
  >([]);
  const [selectedExisting, setSelectedExisting] = useState<Set<string>>(
    new Set()
  );
  const [loadingExisting, setLoadingExisting] = useState(false);

  // Load available models when dialog opens
  useEffect(() => {
    if (open) {
      getSynthesisModels()
        .then(setAvailableModels)
        .catch(() => setAvailableModels([]));
    }
  }, [open]);

  // Load existing syntheses when super mode is selected
  useEffect(() => {
    if (mode === "super" && open) {
      setLoadingExisting(true);
      listSyntheses(0, 50)
        .then((data) => setExistingSyntheses(data.items))
        .catch(() => setExistingSyntheses([]))
        .finally(() => setLoadingExisting(false));
    }
  }, [mode, open]);

  const toggleExisting = (id: string) => {
    setSelectedExisting((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSubmit = async () => {
    if (!topic.trim()) return;
    setCreating(true);
    try {
      let result;
      const selectedModel = modelId !== "default" ? modelId : undefined;
      if (mode === "super") {
        result = await createSuperSynthesis({
          topic: topic.trim(),
          existing_synthesis_ids: Array.from(selectedExisting),
          scope_count: scopeCount,
          visibility,
          model_id: selectedModel,
        });
      } else {
        result = await createSynthesis({
          topic: topic.trim(),
          exploration_budget: budget,
          visibility,
          model_id: selectedModel,
        });
      }
      setWorkflowRunId(result.workflow_run_id);
    } catch (err) {
      console.error("Failed to create synthesis:", err);
    } finally {
      setCreating(false);
    }
  };

  const handleProgressComplete = useCallback(() => {
    onOpenChange(false);
    setWorkflowRunId(null);
    setTopic("");
    setModelId("default");
    setSelectedExisting(new Set());
    onCreated();
  }, [onOpenChange, onCreated]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{workflowRunId ? "Synthesis Progress" : "New Synthesis"}</DialogTitle>
          <DialogDescription>
            {workflowRunId
              ? "Your synthesis is being generated."
              : "Create a research document from the knowledge graph."}
          </DialogDescription>
        </DialogHeader>

        {workflowRunId ? (
          <div className="py-4">
            <SynthesisProgress
              workflowRunId={workflowRunId}
              onComplete={handleProgressComplete}
            />
          </div>
        ) : (
        <>
        <div className="space-y-4 py-4">
          {/* Mode selector */}
          <div className="space-y-2">
            <Label>Type</Label>
            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setMode("synthesis")}
                className={cn(
                  "flex flex-col items-start gap-1 rounded-lg border p-3 text-left transition-colors",
                  mode === "synthesis"
                    ? "border-primary bg-primary/5"
                    : "hover:bg-accent"
                )}
              >
                <div className="flex items-center gap-2">
                  <FileText className="size-4" />
                  <span className="text-sm font-medium">Synthesis</span>
                </div>
                <p className="text-xs text-muted-foreground">
                  Single agent explores the graph and writes a document.
                </p>
              </button>
              <button
                type="button"
                onClick={() => setMode("super")}
                className={cn(
                  "flex flex-col items-start gap-1 rounded-lg border p-3 text-left transition-colors",
                  mode === "super"
                    ? "border-primary bg-primary/5"
                    : "hover:bg-accent"
                )}
              >
                <div className="flex items-center gap-2">
                  <Layers className="size-4" />
                  <span className="text-sm font-medium">Super-Synthesis</span>
                </div>
                <p className="text-xs text-muted-foreground">
                  Multiple agents investigate different scopes, then combine.
                </p>
              </button>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="topic">Topic</Label>
            <Input
              id="topic"
              placeholder="e.g., Climate change mitigation strategies"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
            />
          </div>

          {/* Model selector */}
          {availableModels.length > 0 && (
            <div className="space-y-2">
              <Label>Model</Label>
              <Select value={modelId} onValueChange={setModelId}>
                <SelectTrigger>
                  <SelectValue placeholder="Server Default" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="default">Server Default</SelectItem>
                  {availableModels.map((m) => (
                    <SelectItem key={m.model_id} value={m.model_id}>
                      {m.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Budget only shown for regular synthesis */}
          {mode === "synthesis" && (
            <div className="space-y-2">
              <Label htmlFor="budget">Exploration Budget (nodes)</Label>
              <Input
                id="budget"
                type="number"
                min={5}
                max={100}
                value={budget}
                onChange={(e) => setBudget(parseInt(e.target.value) || 20)}
              />
              <p className="text-xs text-muted-foreground">
                How many nodes the agent can visit during investigation.
              </p>
            </div>
          )}

          {mode === "super" && (
            <>
              <p className="text-xs text-muted-foreground rounded-md bg-muted p-3">
                The super-synthesizer will search the graph, plan thematic
                scopes, run a separate synthesis agent for each, and combine
                all findings into a comprehensive meta-synthesis.
              </p>

              <div className="space-y-2">
                <Label htmlFor="scopeCount">Number of Scopes</Label>
                <Input
                  id="scopeCount"
                  type="number"
                  min={0}
                  max={10}
                  value={scopeCount}
                  onChange={(e) =>
                    setScopeCount(parseInt(e.target.value) || 0)
                  }
                />
                <p className="text-xs text-muted-foreground">
                  0 = let the AI decide (3-7 scopes). Set a number to
                  enforce exactly that many sub-investigations.
                </p>
              </div>

              {/* Include existing syntheses */}
              <div className="space-y-2">
                <Label>
                  Include Existing Research{" "}
                  <span className="text-muted-foreground font-normal">
                    (optional)
                  </span>
                </Label>
                <p className="text-xs text-muted-foreground">
                  Select previous syntheses to include in the meta-synthesis.
                </p>
                {loadingExisting ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
                    <Loader2 className="size-3 animate-spin" />
                    Loading...
                  </div>
                ) : existingSyntheses.length === 0 ? (
                  <p className="text-xs text-muted-foreground italic py-1">
                    No existing syntheses available.
                  </p>
                ) : (
                  <div className="max-h-40 overflow-y-auto space-y-1 rounded-md border p-1">
                    {existingSyntheses.map((item) => {
                      const isSelected = selectedExisting.has(item.id);
                      const { title } = formatSynthesisConcept(item.concept);
                      return (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => toggleExisting(item.id)}
                          className={cn(
                            "flex items-center gap-2 w-full text-left rounded px-2 py-1.5 text-xs transition-colors",
                            isSelected
                              ? "bg-primary/10 text-primary"
                              : "hover:bg-muted"
                          )}
                        >
                          <div
                            className={cn(
                              "size-4 rounded border flex items-center justify-center shrink-0",
                              isSelected
                                ? "bg-primary border-primary"
                                : "border-muted-foreground/30"
                            )}
                          >
                            {isSelected && (
                              <Check className="size-3 text-primary-foreground" />
                            )}
                          </div>
                          <span className="truncate">{title}</span>
                          {item.created_at && (
                            <span className="text-muted-foreground ml-auto shrink-0">
                              {new Date(
                                item.created_at
                              ).toLocaleDateString()}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
                {selectedExisting.size > 0 && (
                  <p className="text-xs text-muted-foreground">
                    {selectedExisting.size} existing{" "}
                    {selectedExisting.size === 1 ? "synthesis" : "syntheses"}{" "}
                    selected
                  </p>
                )}
              </div>
            </>
          )}

          <div className="space-y-2">
            <Label>Visibility</Label>
            <Select value={visibility} onValueChange={setVisibility}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="public">Public</SelectItem>
                <SelectItem value="private">Private</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!topic.trim() || creating}>
            {creating && <Loader2 className="mr-2 size-4 animate-spin" />}
            {mode === "super" ? "Create Super-Synthesis" : "Create Synthesis"}
          </Button>
        </DialogFooter>
        </>
        )}
      </DialogContent>
    </Dialog>
  );
}
