"use client";

import type { EdgeResponse } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ArrowRight, FileText, Network } from "lucide-react";
import { cn } from "@/lib/utils";
import { JustificationText } from "@/components/shared/JustificationText";

interface NeighborsTabProps {
  edges: EdgeResponse[];
  currentNodeId: string;
  onNodeSelect?: (nodeId: string) => void;
}

const relationshipColors: Record<string, string> = {
  related: "bg-gray-100 text-gray-800 dark:bg-gray-900 dark:text-gray-200",
  contradicts:
    "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
};

function truncateId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}...` : id;
}

export function NeighborsTab({
  edges,
  currentNodeId,
  onNodeSelect,
}: NeighborsTabProps) {
  if (edges.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Network className="h-10 w-10 mb-3 opacity-50" />
        <p>No connections.</p>
      </div>
    );
  }

  // Sort edges: contradicts (dialectic pair) first, then by weight descending
  const sortedEdges = [...edges].sort((a, b) => {
    const aIsContradicts = a.relationship_type === "contradicts" ? 1 : 0;
    const bIsContradicts = b.relationship_type === "contradicts" ? 1 : 0;
    if (aIsContradicts !== bIsContradicts) return bIsContradicts - aIsContradicts;
    return Math.abs(b.weight) - Math.abs(a.weight);
  });

  return (
    <div className="space-y-2">
      {sortedEdges.map((edge) => {
        const isOutgoing = edge.source_node_id === currentNodeId;
        const neighborId = isOutgoing
          ? edge.target_node_id
          : edge.source_node_id;
        const neighborConcept = isOutgoing
          ? edge.target_node_concept
          : edge.source_node_concept;
        const isContradicts = edge.relationship_type === "contradicts";

        return (
          <Card
            key={edge.id}
            className={cn(
              isContradicts &&
                "border-amber-300 dark:border-amber-700"
            )}
          >
            <CardContent className="py-3 space-y-2">
              <div className="flex items-center justify-between">
                <Badge
                  className={cn(
                    "text-xs capitalize",
                    relationshipColors[edge.relationship_type] ?? ""
                  )}
                  variant="secondary"
                >
                  {edge.relationship_type.replace(/_/g, " ")}
                </Badge>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">
                    {edge.supporting_fact_ids.length} {edge.supporting_fact_ids.length === 1 ? "fact" : "facts"}
                  </span>
                  <Badge variant="outline" className="text-xs">
                    {isOutgoing ? "outgoing" : "incoming"}
                  </Badge>
                </div>
              </div>

              <div className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground font-mono text-xs">
                  {edge.source_node_concept ?? truncateId(edge.source_node_id)}
                </span>
                <ArrowRight className="h-3 w-3 text-muted-foreground shrink-0" />
                <span className="text-muted-foreground font-mono text-xs">
                  {edge.target_node_concept ?? truncateId(edge.target_node_id)}
                </span>
              </div>

              {edge.justification && (
                <JustificationText
                  text={edge.justification}
                  className="text-xs text-muted-foreground italic block"
                />
              )}

              {edge.supporting_fact_ids.length > 0 && (
                <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                  <FileText className="size-3" />
                  {edge.supporting_fact_ids.length} supporting fact{edge.supporting_fact_ids.length !== 1 ? "s" : ""}
                </span>
              )}

              <Button
                variant={isContradicts ? "outline" : "ghost"}
                size="sm"
                className={cn(
                  "w-full text-xs",
                  isContradicts && "border-amber-300 text-amber-700 hover:bg-amber-50 dark:border-amber-700 dark:text-amber-300 dark:hover:bg-amber-950"
                )}
                onClick={() => onNodeSelect?.(neighborId)}
                disabled={!onNodeSelect}
              >
                {isContradicts
                  ? `View ${neighborConcept ?? "Dialectic Pair"} →`
                  : `Go to ${neighborConcept ?? truncateId(neighborId)}`}
              </Button>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
