"use client";

import { useState } from "react";
import { Loader2, FileText, Layers } from "lucide-react";
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
import { createSynthesis, createSuperSynthesis } from "@/lib/api";

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
  const [visibility, setVisibility] = useState("public");
  const [creating, setCreating] = useState(false);

  const handleSubmit = async () => {
    if (!topic.trim()) return;
    setCreating(true);
    try {
      if (mode === "super") {
        await createSuperSynthesis({
          topic: topic.trim(),
          visibility,
        });
      } else {
        await createSynthesis({
          topic: topic.trim(),
          exploration_budget: budget,
          visibility,
        });
      }
      onOpenChange(false);
      setTopic("");
      onCreated();
    } catch (err) {
      console.error("Failed to create synthesis:", err);
    } finally {
      setCreating(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New Synthesis</DialogTitle>
          <DialogDescription>
            Create a research document from the knowledge graph.
          </DialogDescription>
        </DialogHeader>
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
            <p className="text-xs text-muted-foreground rounded-md bg-muted p-3">
              The super-synthesizer will automatically search the graph, plan
              3-7 thematic scopes, run a separate synthesis agent for each, and
              then combine all findings into a comprehensive meta-synthesis.
            </p>
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
      </DialogContent>
    </Dialog>
  );
}
