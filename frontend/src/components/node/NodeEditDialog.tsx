"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import type { NodeResponse } from "@/types";

interface NodeEditDialogProps {
  open: boolean;
  node: NodeResponse;
  onClose: () => void;
  onSaved: () => void;
}

export function NodeEditDialog({
  open,
  node,
  onClose,
  onSaved,
}: NodeEditDialogProps) {
  const [concept, setConcept] = useState(node.concept);
  const [attractor, setAttractor] = useState(node.attractor ?? "");
  const [maxTokens, setMaxTokens] = useState(node.max_content_tokens);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setIsSaving(true);
    setError(null);
    const updates: Record<string, unknown> = {};
    if (concept !== node.concept) updates.concept = concept;
    if (attractor !== (node.attractor ?? ""))
      updates.attractor = attractor || null;
    if (maxTokens !== node.max_content_tokens)
      updates.max_content_tokens = maxTokens;

    if (Object.keys(updates).length === 0) {
      onClose();
      return;
    }

    try {
      await api.nodes.update(node.id, updates);
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit Node</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Concept</label>
            <Input
              value={concept}
              onChange={(e) => setConcept(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Attractor</label>
            <Input
              value={attractor}
              onChange={(e) => setAttractor(e.target.value)}
              placeholder="Optional attractor"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Max Content Tokens</label>
            <Input
              type="number"
              value={maxTokens}
              onChange={(e) => setMaxTokens(Number(e.target.value))}
              min={100}
              max={10000}
            />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isSaving}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={isSaving}>
            {isSaving && <Loader2 className="mr-2 size-4 animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
