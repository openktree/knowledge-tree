"use client";

import { EdgeCandidateListView } from "@/components/edge-candidate/EdgeCandidateListView";

export default function EdgeCandidatesPage() {
  return (
    <div className="flex flex-col h-full">
      <div className="px-6 pt-6 pb-0">
        <h1 className="text-2xl font-bold tracking-tight">Edge Candidates</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Proposed relationships between nodes discovered during ingestion.
          Review candidates to create verified edges in the graph.
        </p>
      </div>

      <div className="flex-1 min-h-0 mt-4">
        <EdgeCandidateListView />
      </div>
    </div>
  );
}
