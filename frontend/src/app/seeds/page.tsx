"use client";

import { Suspense } from "react";
import { SeedListView } from "@/components/seed/SeedListView";

export default function SeedsPage() {
  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0">
        <h1 className="text-2xl font-bold tracking-tight">Seeds</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Seeds are entities and concepts automatically extracted during
          ingestion. Review them here and promote promising seeds into full
          graph nodes.
        </p>
      </div>

      <div className="flex-1 min-h-0 mt-4">
        <Suspense>
          <SeedListView />
        </Suspense>
      </div>
    </div>
  );
}
