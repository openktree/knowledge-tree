"use client";

import { X, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { SentenceFactsBySourceResponse } from "@/types";

interface SynthesisFactPanelProps {
  position: number;
  facts: SentenceFactsBySourceResponse[] | null;
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
      <CardContent className="space-y-4 max-h-[70vh] overflow-y-auto">
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
          facts.map((sourceGroup) => (
            <div key={sourceGroup.source_id} className="space-y-2">
              <div className="flex items-center gap-2">
                <h4 className="text-xs font-medium truncate">
                  {sourceGroup.source_title || "Unknown Source"}
                </h4>
                <Badge variant="outline" className="text-[10px] shrink-0">
                  {sourceGroup.facts.length}
                </Badge>
              </div>
              {sourceGroup.source_uri && (
                <a
                  href={sourceGroup.source_uri}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[10px] text-muted-foreground hover:underline block truncate"
                >
                  {sourceGroup.source_uri}
                </a>
              )}
              <ul className="space-y-1.5">
                {sourceGroup.facts.map((fact) => (
                  <li key={fact.fact_id} className="text-xs leading-relaxed">
                    <Badge variant="secondary" className="text-[9px] mr-1">
                      {fact.fact_type}
                    </Badge>
                    <span className="text-muted-foreground">
                      ({(fact.embedding_distance * 100).toFixed(0)}%)
                    </span>{" "}
                    {fact.content}
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}
