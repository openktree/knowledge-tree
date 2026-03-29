"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { getSynthesis, deleteSynthesis, regenerateSynthesis } from "@/lib/api";
import { SynthesisDocument } from "@/components/synthesis/SynthesisDocument";
import { SynthesisProgress } from "@/components/synthesis/SynthesisProgress";
import { ExportButtons } from "@/components/synthesis/ExportPDF";
import type { SynthesisDocumentResponse } from "@/types";

export default function SynthesisDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;
  const [document, setDocument] = useState<SynthesisDocumentResponse | null>(
    null
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [workflowRunId, setWorkflowRunId] = useState<string | null>(null);

  const fetchDocument = useCallback(async () => {
    if (!id) return;
    try {
      const doc = await getSynthesis(id);
      setDocument(doc);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load synthesis"
      );
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchDocument();
  }, [fetchDocument]);

  const handleRegenerateComplete = useCallback(async () => {
    setWorkflowRunId(null);
    setRegenerating(false);
    await fetchDocument();
  }, [fetchDocument]);

  const handleDelete = async () => {
    if (!confirm("Delete this synthesis? This cannot be undone.")) return;
    setDeleting(true);
    try {
      await deleteSynthesis(id);
      router.push("/investigate");
    } catch (err) {
      console.error("Failed to delete synthesis:", err);
      setDeleting(false);
    }
  };

  const handleRegenerate = async () => {
    setRegenerating(true);
    try {
      const result = await regenerateSynthesis(id);
      setWorkflowRunId(result.workflow_run_id);
    } catch (err) {
      console.error("Failed to regenerate synthesis:", err);
      setRegenerating(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !document) {
    return (
      <div className="mx-auto max-w-4xl py-8 px-4">
        <Button variant="ghost" asChild className="mb-4">
          <Link href="/investigate">
            <ArrowLeft className="mr-2 size-4" />
            Back to Investigations
          </Link>
        </Button>
        <p className="text-destructive">{error || "Synthesis not found"}</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1600px] py-8 px-4 sm:px-6">
      {/* Top bar */}
      <div className="flex items-center justify-between mb-6 max-w-4xl mx-auto">
        <Button variant="ghost" size="sm" asChild className="text-muted-foreground hover:text-foreground -ml-2">
          <Link href="/investigate">
            <ArrowLeft className="mr-1.5 size-3.5" />
            Investigations
          </Link>
        </Button>
        <div className="flex items-center gap-1">
          <ExportButtons documentId={id} concept={document.concept} />
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-destructive"
            onClick={handleDelete}
            disabled={deleting}
          >
            {deleting ? (
              <Loader2 className="mr-1.5 size-3.5 animate-spin" />
            ) : (
              <Trash2 className="mr-1.5 size-3.5" />
            )}
            Delete
          </Button>
        </div>
      </div>

      {workflowRunId && (
        <div className="max-w-4xl mx-auto mb-6">
          <SynthesisProgress workflowRunId={workflowRunId} onComplete={handleRegenerateComplete} />
        </div>
      )}

      <SynthesisDocument
        document={document}
        onRegenerate={document.status === "error" ? handleRegenerate : undefined}
        isRegenerating={regenerating}
      />
    </div>
  );
}
