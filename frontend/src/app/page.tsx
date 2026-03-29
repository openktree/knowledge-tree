"use client";

import Link from "next/link";
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
            Build and investigate a knowledge graph grounded in real sources.
            Ingest data to grow the graph, then explore findings through
            synthesis agents or direct browsing.
          </p>
          <p className="text-xs text-muted-foreground/70 mt-2">
            Suggested flow: Browse existing data → identify gaps → grow the
            graph with new sources → investigate via synthesis agents or at your
            own pace using the MCP.
          </p>
        </div>

        {/* Navigation cards */}
        <div className="grid gap-4 sm:grid-cols-2">
          <Link
            href="/investigate"
            className="rounded-lg border p-6 hover:bg-accent transition-colors"
          >
            <h2 className="font-semibold mb-1">Investigate</h2>
            <p className="text-sm text-muted-foreground">
              Launch synthesis agents to explore topics by integrating
              information across your knowledge graph.
            </p>
          </Link>
          <Link
            href="/grow-graph"
            className="rounded-lg border p-6 hover:bg-accent transition-colors"
          >
            <h2 className="font-semibold mb-1">Grow Graph</h2>
            <p className="text-sm text-muted-foreground">
              Upload documents or discover web sources to extract facts and
              expand the knowledge graph.
            </p>
          </Link>
          <Link
            href="/nodes"
            className="rounded-lg border p-6 hover:bg-accent transition-colors"
          >
            <h2 className="font-semibold mb-1">Browse Graph</h2>
            <p className="text-sm text-muted-foreground">
              Explore nodes, edges, and relationships directly in the graph.
            </p>
          </Link>
          <Link
            href="/seeds"
            className="rounded-lg border p-6 hover:bg-accent transition-colors"
          >
            <h2 className="font-semibold mb-1">Seeds</h2>
            <p className="text-sm text-muted-foreground">
              Review extracted entities and concepts awaiting promotion to full
              nodes.
            </p>
          </Link>
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
