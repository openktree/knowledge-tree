"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { QueryBudgetControls } from "@/components/query/QueryBudgetControls";
import { api } from "@/lib/api";
import type { NodeResponse } from "@/types";

interface ResearchNodeDialogProps {
  node: NodeResponse;
  onClose: () => void;
}

export function ResearchNodeDialog({
  node,
  onClose,
}: ResearchNodeDialogProps) {
  const router = useRouter();

  const [query, setQuery] = useState(
    `Research and expand knowledge about ${node.concept}`,
  );
  const [navBudget, setNavBudget] = useState(50);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    setIsSubmitting(true);
    setError(null);

    try {
      const response = await api.conversations.create({
        message: query,
        nav_budget: navBudget,
        explore_budget: 0,
        mode: "query",
        title: `Research: ${node.concept}`,
      });
      onClose();
      router.push(`/conversation/${response.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start research");
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Research Node</DialogTitle>
          <DialogDescription>
            Launch a research conversation to expand knowledge about this node.
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="max-h-[60vh]">
          <div className="space-y-4 px-1">
            <div className="space-y-2">
              <Label htmlFor="research-query">Query</Label>
              <Textarea
                id="research-query"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                rows={3}
              />
            </div>
            <QueryBudgetControls
              navBudget={navBudget}
              onNavBudgetChange={setNavBudget}
            />
            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>
        </ScrollArea>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={isSubmitting || !query.trim()}
          >
            {isSubmitting && <Loader2 className="mr-2 size-4 animate-spin" />}
            Start Research
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
