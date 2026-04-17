"use client";

import { use } from "react";
import { useNodeDetail } from "@/hooks/useNodeDetail";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui/tabs";
import { Loader2 } from "lucide-react";
import { DimensionsTab } from "@/components/node/DimensionsTab";
import { FactsTab } from "@/components/node/FactsTab";
import { HistoryTab } from "@/components/node/HistoryTab";
import { NeighborsTab } from "@/components/node/NeighborsTab";

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function NodeDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const {
    node,
    dimensions,
    facts,
    edges,
    history,
    isLoading,
    error,
  } = useNodeDetail(id);

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-4">
        <h1 className="text-2xl font-bold tracking-tight">
          {isLoading && !node ? "Loading..." : node?.concept ?? "Node Not Found"}
        </h1>
        <p className="text-sm text-muted-foreground mt-1">Node Detail</p>
      </div>

      {isLoading && !node && (
        <div className="flex-1 flex items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && (
        <div className="px-6 py-3">
          <p className="text-sm text-destructive">{error}</p>
        </div>
      )}

      {node && (
        <div className="flex-1 flex flex-col min-h-0 px-6">
          <div className="flex flex-wrap items-center gap-2 pb-4">
            <Badge variant="secondary">
              Richness: {node.richness?.toFixed(2) ?? "N/A"}
            </Badge>
            <Badge variant="outline">
              Accessed: {node.access_count ?? 0}
            </Badge>
            <Badge variant="outline">
              Updates: {node.update_count ?? 0}
            </Badge>
            {node.created_at && (
              <span className="text-xs text-muted-foreground">
                Created: {formatDate(node.created_at)}
              </span>
            )}
            {node.updated_at && (
              <span className="text-xs text-muted-foreground">
                Updated: {formatDate(node.updated_at)}
              </span>
            )}
          </div>

          <Separator />

          <Tabs defaultValue="dimensions" className="flex-1 flex flex-col min-h-0 pt-4">
            <TabsList>
              <TabsTrigger value="dimensions">Dimensions</TabsTrigger>
              <TabsTrigger value="facts">Facts</TabsTrigger>
              <TabsTrigger value="history">History</TabsTrigger>
              <TabsTrigger value="neighbors">Neighbors</TabsTrigger>
            </TabsList>

            <div className="flex-1 overflow-y-auto pt-4 pb-6">
              <TabsContent value="dimensions" className="mt-0">
                <DimensionsTab dimensions={dimensions} />
              </TabsContent>
              <TabsContent value="facts" className="mt-0">
                <FactsTab facts={facts} />
              </TabsContent>
              <TabsContent value="history" className="mt-0">
                <HistoryTab history={history} />
              </TabsContent>
              <TabsContent value="neighbors" className="mt-0">
                <NeighborsTab
                  edges={edges}
                  currentNodeId={id}
                />
              </TabsContent>
            </div>
          </Tabs>
        </div>
      )}
    </div>
  );
}
