"use client";

import { X, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { SentenceFactResponse } from "@/types";

interface SynthesisFactPanelProps {
  position: number;
  facts: SentenceFactResponse[] | null;
  loading: boolean;
  onClose: () => void;
}

export function SynthesisFactPanel({
  position,
  facts,
  loading,
  onClose,
}: SynthesisFactPanelProps) {
  return (
    <Card className="sticky top-4">
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <CardTitle className="text-sm">
          Facts for sentence {position + 1}
        </CardTitle>
        <Button variant="ghost" size="icon" className="size-6" onClick={onClose}>
          <X className="size-4" />
        </Button>
      </CardHeader>
      <CardContent className="space-y-2 max-h-[70vh] overflow-y-auto">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Loading facts...
          </div>
        ) : !facts || facts.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No facts linked to this sentence.
          </p>
        ) : (
          <ul className="space-y-2">
            {facts.map((fact) => (
              <li key={fact.fact_id} className="text-xs leading-relaxed">
                <span className="text-muted-foreground">
                  ({(fact.embedding_distance * 100).toFixed(0)}% match)
                </span>{" "}
                <a
                  href={`/facts/${fact.fact_id}`}
                  className="text-primary hover:underline"
                >
                  {fact.fact_id.slice(0, 8)}...
                </a>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
