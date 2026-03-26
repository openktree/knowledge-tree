"use client";

import { TreePine } from "lucide-react";
import { useAuth } from "@/contexts/auth";

export default function HomePage() {
  const { user } = useAuth();

  return (
    <div className="flex min-h-full flex-col items-center justify-start px-4 pt-24 pb-12">
      <main className="w-full max-w-2xl space-y-8">
        {/* Header */}
        <div className="space-y-3 text-center">
          <div className="flex items-center justify-center gap-3">
            <TreePine className="size-8 text-primary" />
            <h1 className="text-4xl font-bold tracking-tight">
              Knowledge Tree
            </h1>
          </div>
          <p className="text-muted-foreground">
            A knowledge integration system that builds understanding from raw
            external data. Ingest sources, grow the graph, and synthesize
            research documents.
          </p>
        </div>

        {/* Navigation cards */}
        <div className="grid gap-4 sm:grid-cols-2">
          <a
            href="/syntheses"
            className="rounded-lg border p-6 hover:bg-accent transition-colors"
          >
            <h2 className="font-semibold mb-1">Syntheses</h2>
            <p className="text-sm text-muted-foreground">
              Create and view research synthesis documents from the graph.
            </p>
          </a>
          <a
            href="/research"
            className="rounded-lg border p-6 hover:bg-accent transition-colors"
          >
            <h2 className="font-semibold mb-1">Ingest Sources</h2>
            <p className="text-sm text-muted-foreground">
              Upload files or add links to grow the knowledge graph.
            </p>
          </a>
          <a
            href="/nodes"
            className="rounded-lg border p-6 hover:bg-accent transition-colors"
          >
            <h2 className="font-semibold mb-1">Browse Graph</h2>
            <p className="text-sm text-muted-foreground">
              Explore nodes, edges, facts, and seeds in the knowledge graph.
            </p>
          </a>
          <a
            href="/seeds"
            className="rounded-lg border p-6 hover:bg-accent transition-colors"
          >
            <h2 className="font-semibold mb-1">Seeds</h2>
            <p className="text-sm text-muted-foreground">
              View extracted entities and concepts awaiting promotion.
            </p>
          </a>
        </div>

        {user && (
          <p className="text-xs text-center text-muted-foreground">
            Signed in as {user.email}
          </p>
        )}
      </main>
    </div>
  );
}
