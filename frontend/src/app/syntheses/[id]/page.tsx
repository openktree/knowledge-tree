"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getSynthesis } from "@/lib/api";
import { SynthesisDocument } from "@/components/synthesis/SynthesisDocument";
import type { SynthesisDocumentResponse } from "@/types";

export default function SynthesisDetailPage() {
  const params = useParams();
  const id = params.id as string;
  const [document, setDocument] = useState<SynthesisDocumentResponse | null>(
    null
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDocument = useCallback(async () => {
    if (!id) return;
    try {
      const doc = await getSynthesis(id);
      setDocument(doc);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load synthesis");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchDocument();
  }, [fetchDocument]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !document) {
    return (
      <div className="container max-w-4xl py-8">
        <Button variant="ghost" asChild className="mb-4">
          <Link href="/syntheses">
            <ArrowLeft className="mr-2 size-4" />
            Back to Syntheses
          </Link>
        </Button>
        <p className="text-destructive">{error || "Synthesis not found"}</p>
      </div>
    );
  }

  return (
    <div className="container max-w-6xl py-8">
      <Button variant="ghost" asChild className="mb-4">
        <Link href="/syntheses">
          <ArrowLeft className="mr-2 size-4" />
          Back to Syntheses
        </Link>
      </Button>
      <SynthesisDocument document={document} />
    </div>
  );
}
