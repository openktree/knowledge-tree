"use client";

import { useState } from "react";
import { Loader2, Check, ChevronsUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { FactType } from "@/types";
import type { FactResponse } from "@/types";

const FACT_TYPES = Object.values(FactType);

interface FactEditDialogProps {
  open: boolean;
  fact: FactResponse;
  onClose: () => void;
  onSaved: () => void;
}

export function FactEditDialog({
  open,
  fact,
  onClose,
  onSaved,
}: FactEditDialogProps) {
  const [content, setContent] = useState(fact.content);
  const [factType, setFactType] = useState(fact.fact_type);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setIsSaving(true);
    setError(null);
    const updates: Record<string, unknown> = {};
    if (content !== fact.content) updates.content = content;
    if (factType !== fact.fact_type) updates.fact_type = factType;

    if (Object.keys(updates).length === 0) {
      onClose();
      return;
    }

    try {
      await api.facts.update(fact.id, updates);
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
          <DialogTitle>Edit Fact</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Content</label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={4}
              className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Fact Type</label>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  className="w-full justify-between"
                >
                  {factType}
                  <ChevronsUpDown className="ml-2 size-4 opacity-50" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent className="w-full min-w-[200px]">
                {FACT_TYPES.map((type) => (
                  <DropdownMenuItem
                    key={type}
                    onClick={() => setFactType(type)}
                  >
                    {factType === type && (
                      <Check className="mr-2 size-4" />
                    )}
                    <span className={factType !== type ? "ml-6" : ""}>
                      {type}
                    </span>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
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
