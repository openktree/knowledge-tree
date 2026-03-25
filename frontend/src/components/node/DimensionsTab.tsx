"use client";

import type { DimensionResponse } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { BrainCircuit, CheckCircle, FileEdit } from "lucide-react";
import { cn } from "@/lib/utils";

interface DimensionsTabProps {
  dimensions: DimensionResponse[];
  onConceptClick?: (concept: string) => void;
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function DimensionsTab({
  dimensions,
  onConceptClick,
}: DimensionsTabProps) {
  if (dimensions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <BrainCircuit className="h-10 w-10 mb-3 opacity-50" />
        <p>No dimensions generated yet.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {dimensions.map((dimension) => (
        <Card key={dimension.id}>
          <CardContent className="pt-4 space-y-3">
            <div className="flex items-center justify-between flex-wrap gap-1">
              <div className="flex items-center gap-1.5">
                <Badge variant="secondary">{dimension.model_id}</Badge>
                {dimension.is_definitive ? (
                  <Badge variant="default" className="gap-1 bg-green-600 text-xs">
                    <CheckCircle className="h-3 w-3" />
                    Definitive
                  </Badge>
                ) : (
                  <Badge variant="outline" className="gap-1 text-xs">
                    <FileEdit className="h-3 w-3" />
                    Draft
                  </Badge>
                )}
                {dimension.fact_count > 0 && (
                  <Badge variant="outline" className="text-xs">
                    {dimension.fact_count} facts
                  </Badge>
                )}
              </div>
              <div className="flex items-center gap-3">
                <Badge variant="outline" className="text-xs">
                  Confidence: {(dimension.confidence * 100).toFixed(0)}%
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {formatDate(dimension.generated_at)}
                </span>
              </div>
            </div>

            <Separator />

            <p className="text-sm leading-relaxed whitespace-pre-wrap">
              {dimension.content}
            </p>

            {dimension.suggested_concepts &&
              dimension.suggested_concepts.length > 0 && (
                <>
                  <Separator />
                  <div className="flex flex-wrap gap-1.5">
                    <span className="text-xs text-muted-foreground mr-1 self-center">
                      Suggested:
                    </span>
                    {dimension.suggested_concepts.map((concept) => (
                      <Badge
                        key={concept}
                        variant="outline"
                        className={cn(
                          onConceptClick &&
                            "cursor-pointer hover:bg-accent transition-colors"
                        )}
                        onClick={() => onConceptClick?.(concept)}
                      >
                        {concept}
                      </Badge>
                    ))}
                  </div>
                </>
              )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
