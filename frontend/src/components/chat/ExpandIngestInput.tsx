"use client";

import { useState, useCallback } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ExpandIngestInputProps {
  existingNodeCount: number;
  onSendMessage: (
    message: string,
    navBudget: number,
    exploreBudget: number,
  ) => void;
  disabled?: boolean;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ExpandIngestInput({
  existingNodeCount,
  onSendMessage,
  disabled = false,
}: ExpandIngestInputProps) {
  const [navBudget, setNavBudget] = useState(50);

  const handleExpand = useCallback(() => {
    if (disabled) return;
    onSendMessage(
      `Expand ingestion (+${navBudget} more nodes)`,
      navBudget,
      0,
    );
  }, [disabled, onSendMessage, navBudget]);

  return (
    <div className="border-t bg-background p-3 space-y-3">
      <div className="text-xs text-muted-foreground">
        {existingNodeCount} nodes built so far
      </div>

      <div className="flex items-center gap-3">
        <span className="text-xs text-muted-foreground w-24 shrink-0">
          Budget: {navBudget} nodes
        </span>
        <Slider
          value={[navBudget]}
          min={10}
          max={200}
          step={10}
          onValueChange={([v]) => setNavBudget(v)}
        />
      </div>

      <Button
        onClick={handleExpand}
        disabled={disabled}
        className="w-full"
        variant="outline"
      >
        <Plus className="h-4 w-4 mr-2" />
        Expand Ingestion (+{navBudget} nodes)
      </Button>
    </div>
  );
}
