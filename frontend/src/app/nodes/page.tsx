"use client";

import { Suspense, useCallback, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import dynamic from "next/dynamic";
import { toast } from "sonner";
import { useAuth } from "@/contexts/auth";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui/tabs";
import { NodeListView } from "@/components/node/NodeListView";

const GraphExplorer = dynamic(
  () => import("@/components/graph/GraphExplorer"),
  { ssr: false },
);

function NodesContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const activeTab = searchParams.get("tab") ?? "list";
  const initialSeedIds = useMemo(() => {
    const raw = searchParams.get("seeds");
    if (!raw) return undefined;
    const ids = raw.split(",").filter(Boolean);
    return ids.length > 0 ? ids : undefined;
  }, [searchParams]);

  const initialCompareTargetId = useMemo(() => {
    return searchParams.get("compare") ?? undefined;
  }, [searchParams]);

  const setActiveTab = useCallback(
    (tab: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (tab === "list") {
        params.delete("tab");
        params.delete("seeds");
      } else {
        params.set("tab", tab);
      }
      params.delete("selected");
      const qs = params.toString();
      router.replace(qs ? `/nodes?${qs}` : "/nodes", { scroll: false });
    },
    [router, searchParams],
  );

  const handleSeedsChange = useCallback(
    (seedIds: string[]) => {
      const params = new URLSearchParams(searchParams.toString());
      if (seedIds.length > 0) {
        params.set("seeds", seedIds.join(","));
      } else {
        params.delete("seeds");
      }
      if (!params.has("tab")) params.set("tab", "explorer");
      router.replace(`/nodes?${params.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  const handleCompareChange = useCallback(
    (targetId: string | null) => {
      const params = new URLSearchParams(searchParams.toString());
      if (targetId) {
        params.set("compare", targetId);
      } else {
        params.delete("compare");
      }
      const qs = params.toString();
      router.replace(qs ? `/nodes?${qs}` : "/nodes", { scroll: false });
    },
    [router, searchParams],
  );

  const handleViewInGraph = useCallback(
    (nodeId: string) => {
      const params = new URLSearchParams(searchParams.toString());
      const existing = params.get("seeds");
      const ids = existing ? existing.split(",").filter(Boolean) : [];
      if (!ids.includes(nodeId)) ids.push(nodeId);
      params.set("tab", "explorer");
      params.set("seeds", ids.join(","));
      router.replace(`/nodes?${params.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  return (
    <Tabs value={activeTab} onValueChange={setActiveTab} className="flex flex-col flex-1 min-h-0">
      <div className="px-6 pt-4">
        <TabsList>
          <TabsTrigger value="list">List View</TabsTrigger>
          <TabsTrigger value="explorer">Graph Explorer</TabsTrigger>
        </TabsList>
      </div>

      <TabsContent value="list" className="flex-1 min-h-0 mt-0">
        <NodeListView onViewInGraph={handleViewInGraph} />
      </TabsContent>

      <TabsContent value="explorer" className="flex-1 min-h-0 mt-0">
        <GraphExplorer
          initialSeedIds={initialSeedIds}
          onSeedsChange={handleSeedsChange}
          initialCompareTargetId={initialCompareTargetId}
          onCompareChange={handleCompareChange}
        />
      </TabsContent>
    </Tabs>
  );
}

export default function NodesPage() {
  const { user } = useAuth();
  const [rebuilding, setRebuilding] = useState(false);

  const handleRebuild = async () => {
    setRebuilding(true);
    try {
      await api.graphBuilder.autoBuild();
      toast.success("Rebuild started — outdated nodes will be recalculated");
    } catch {
      toast.error("Failed to start rebuild");
    } finally {
      setRebuilding(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Nodes</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Browse, search, and explore knowledge graph nodes
          </p>
        </div>
        {user?.is_superuser && (
          <Button
            variant="outline"
            size="sm"
            disabled={rebuilding}
            onClick={handleRebuild}
          >
            {rebuilding ? "Rebuilding…" : "Rebuild Outdated Nodes"}
          </Button>
        )}
      </div>

      <Suspense>
        <NodesContent />
      </Suspense>
    </div>
  );
}
