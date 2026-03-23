"use client";

import { Suspense } from "react";
import { FactListView } from "@/components/fact/FactListView";

export default function FactsPage() {
  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0">
        <h1 className="text-2xl font-bold tracking-tight">Facts</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Browse, search, and manage knowledge facts
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
