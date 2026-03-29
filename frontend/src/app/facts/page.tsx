"use client";

import { Suspense } from "react";
import { FactListView } from "@/components/fact/FactListView";

export default function FactsPage() {
  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0">
        <h1 className="text-2xl font-bold tracking-tight">Facts</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Facts are atomic claims extracted from ingested sources — the
          evidence base from which nodes and edges are built.
        </p>
      </div>

      <div className="flex-1 min-h-0 mt-4">
        <Suspense>
          <FactListView />
        </Suspense>
      </div>
    </div>
  );
}
