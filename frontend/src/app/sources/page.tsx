"use client";

import { Suspense } from "react";
import { SourceListView } from "@/components/source/SourceListView";

export default function SourcesPage() {
  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0">
        <h1 className="text-2xl font-bold tracking-tight">Sources</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Sources are the raw documents and web pages you have ingested. Each
          source is decomposed into facts that feed the knowledge graph.
        </p>
      </div>

      <div className="flex-1 min-h-0 mt-4">
        <Suspense>
          <SourceListView />
        </Suspense>
      </div>
    </div>
  );
}
