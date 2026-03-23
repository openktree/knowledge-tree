"use client";

import { use } from "react";
import { EdgeCandidateDetailView } from "@/components/edge-candidate/EdgeCandidateDetailView";

export default function EdgeCandidateDetailPage({
  params,
}: {
  params: Promise<{ seedKeyA: string; seedKeyB: string }>;
}) {
  const { seedKeyA, seedKeyB } = use(params);
  return (
    <EdgeCandidateDetailView
      seedKeyA={decodeURIComponent(seedKeyA)}
      seedKeyB={decodeURIComponent(seedKeyB)}
    />
  );
}
