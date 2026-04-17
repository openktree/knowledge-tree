"use client";

import { useEffect, useState } from "react";
import { useGraph } from "@/contexts/graph";
import { GraphReadOnlyBanner } from "@/components/graphs/GraphReadOnlyBanner";
import { getGraph } from "@/lib/api";
import type { GraphResponse } from "@/types";

interface LayoutProps {
  children: React.ReactNode;
  params: Promise<{ slug: string }>;
}

/**
 * Wraps every `/graphs/[slug]` route with the read-only banner.
 *
 * Prefers the graph already loaded in GraphContext (so we don't double-fetch);
 * falls back to direct GET when the context hasn't seen this slug yet (e.g.
 * superuser opening a graph they aren't listed as a member of).
 */
export default function GraphSlugLayout({ children, params }: LayoutProps) {
  const [slug, setSlug] = useState<string | null>(null);
  const { graphs } = useGraph();
  const [graph, setGraph] = useState<GraphResponse | null>(null);

  useEffect(() => {
    void params.then((p) => setSlug(p.slug));
  }, [params]);

  useEffect(() => {
    if (!slug) return;
    const fromContext = graphs.find((g) => g.slug === slug) ?? null;
    if (fromContext) return;
    getGraph(slug)
      .then(setGraph)
      .catch(() => setGraph(null));
  }, [slug, graphs]);

  const resolved = slug ? graphs.find((g) => g.slug === slug) ?? graph : null;

  return (
    <div className="space-y-3">
      {resolved?.read_only ? <GraphReadOnlyBanner graph={resolved} /> : null}
      {children}
    </div>
  );
}
