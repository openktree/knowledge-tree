"use client";

import { useState, useCallback, useRef, useMemo } from "react";
import {
  Upload,
  X,
  FileText,
  Link2,
  Loader2,
  ChevronLeft,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { ResearchBuildProgress } from "@/components/research/ResearchBuildProgress";
import type { IngestPrepareResponse } from "@/types";

const ACCEPTED_TYPES = ".pdf,.txt,.png,.jpg,.jpeg,.webp";
const MAX_FILE_SIZE_MB = 50;

type Step = "upload" | "preparing" | "confirm" | "processing";
interface UploadedFile {
  file: File;
  id: string;
}

export function SourceUploadForm() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState<Step>("upload");
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [links, setLinks] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [prepareResult, setPrepareResult] =
    useState<IngestPrepareResponse | null>(null);

  // Decompose+build message for progress tracking
  const [decomposeMessageId, setDecomposeMessageId] = useState<string | null>(
    null,
  );

  // Multigraph public-cache opt-out. Defaults to "share with public
  // graph" since that's how the public pool grows. Hidden for file-only
  // uploads because the API forces False server-side.
  const [shareWithPublicGraph, setShareWithPublicGraph] = useState(true);

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const newFiles = Array.from(e.target.files || []);
      const valid: UploadedFile[] = [];

      for (const f of newFiles) {
        if (f.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
          setError(`${f.name} exceeds ${MAX_FILE_SIZE_MB}MB limit`);
          continue;
        }
        valid.push({ file: f, id: crypto.randomUUID() });
      }

      setFiles((prev) => [...prev, ...valid]);
      if (fileInputRef.current) fileInputRef.current.value = "";
    },
    [],
  );

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const newFiles = Array.from(e.dataTransfer.files);
    const valid: UploadedFile[] = [];

    for (const f of newFiles) {
      if (f.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
        setError(`${f.name} exceeds ${MAX_FILE_SIZE_MB}MB limit`);
        continue;
      }
      valid.push({ file: f, id: crypto.randomUUID() });
    }

    setFiles((prev) => [...prev, ...valid]);
  }, []);

  const removeFile = useCallback((id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  }, []);

  const linkCount = links
    .split("\n")
    .filter((l) => l.trim().length > 0).length;

  const canAnalyze = (files.length > 0 || linkCount > 0) && step === "upload";

  const hasLinkSources = useMemo(
    () => prepareResult?.sources.some((s) => s.source_type === "link") ?? false,
    [prepareResult],
  );

  // Step 1: Analyze sources (prepare)
  const handleAnalyze = async () => {
    setStep("preparing");
    setError(null);

    try {
      const formData = new FormData();

      for (const { file } of files) {
        formData.append("files", file);
      }

      if (links.trim()) {
        formData.append("links", links.trim());
      }

      const result = await api.research.prepare(formData);
      setPrepareResult(result);
      setStep("confirm");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed");
      setStep("upload");
    }
  };

  // Step 2: Extract & Build (decompose + auto-build in one workflow)
  const handleExtractAndBuild = async () => {
    if (!prepareResult) return;

    setStep("processing");
    setError(null);

    try {
      const result = await api.research.decompose(
        prepareResult.conversation_id,
        shareWithPublicGraph,
      );
      setDecomposeMessageId(result.message_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start processing");
      setStep("confirm");
    }
  };

  const handleBack = () => {
    setStep("upload");
    setPrepareResult(null);
    setError(null);
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Step: Upload / Preparing */}
      {(step === "upload" || step === "preparing") && (
        <>
          {/* File upload area */}
          <div>
            <Label className="text-sm font-medium">Files</Label>
            <div
              className="mt-2 border-2 border-dashed rounded-lg p-8 text-center cursor-pointer hover:border-primary/50 transition-colors"
              onClick={() => fileInputRef.current?.click()}
              onDrop={handleDrop}
              onDragOver={(e) => e.preventDefault()}
            >
              <Upload className="size-8 mx-auto text-muted-foreground mb-2" />
              <p className="text-sm text-muted-foreground">
                Drag & drop files here or click to browse
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                PDF, TXT, PNG, JPG, WEBP (max {MAX_FILE_SIZE_MB}MB each)
              </p>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={ACCEPTED_TYPES}
                onChange={handleFileChange}
                className="hidden"
              />
            </div>

            {/* File list */}
            {files.length > 0 && (
              <div className="mt-3 space-y-1.5">
                {files.map(({ file, id }) => (
                  <div
                    key={id}
                    className="flex items-center gap-2 text-sm bg-muted/50 rounded px-3 py-2"
                  >
                    <FileText className="size-4 text-muted-foreground shrink-0" />
                    <span className="truncate flex-1">{file.name}</span>
                    <span className="text-xs text-muted-foreground">
                      {(file.size / 1024).toFixed(0)} KB
                    </span>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      onClick={() => removeFile(id)}
                      disabled={step === "preparing"}
                    >
                      <X className="size-3" />
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Links input */}
          <div>
            <Label className="text-sm font-medium">Links</Label>
            <Textarea
              placeholder="Paste URLs, one per line&#10;https://example.com/article&#10;https://example.com/paper.pdf"
              value={links}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                setLinks(e.target.value)
              }
              rows={4}
              className="mt-2 font-mono text-sm"
              disabled={step === "preparing"}
            />
            {linkCount > 0 && (
              <p className="text-xs text-muted-foreground mt-1">
                <Link2 className="size-3 inline mr-1" />
                {linkCount} link{linkCount !== 1 ? "s" : ""}
              </p>
            )}
          </div>

          {/* Error display */}
          {error && (
            <div className="text-sm text-red-500 bg-red-50 dark:bg-red-950/20 rounded px-3 py-2">
              {error}
            </div>
          )}

          {/* Analyze button */}
          <Button
            onClick={handleAnalyze}
            disabled={!canAnalyze}
            className="w-full"
            size="lg"
          >
            {step === "preparing" ? (
              <>
                <Loader2 className="size-4 mr-2 animate-spin" />
                Analyzing sources...
              </>
            ) : (
              <>
                <Upload className="size-4 mr-2" />
                Analyze Sources
                {files.length > 0 || linkCount > 0
                  ? ` (${files.length + linkCount} source${files.length + linkCount !== 1 ? "s" : ""})`
                  : ""}
              </>
            )}
          </Button>
        </>
      )}

      {/* Step: Confirm */}
      {step === "confirm" && prepareResult && (
        <>
          <div className="flex items-center gap-2 mb-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleBack}
              className="gap-1"
            >
              <ChevronLeft className="size-4" />
              Back
            </Button>
            <h3 className="font-medium">Review & Confirm</h3>
          </div>

          {/* Source list with token estimates */}
          <div className="border rounded-lg p-4 space-y-3">
            <h4 className="text-sm font-medium">
              Sources ({prepareResult.sources.length})
            </h4>

            <div className="space-y-1 max-h-80 overflow-y-auto">
              {prepareResult.sources.map((source) => (
                <div
                  key={source.id}
                  className="flex items-center gap-2 px-3 py-2 text-sm border rounded"
                >
                  <FileText className="size-4 text-muted-foreground shrink-0" />
                  <span className="truncate flex-1">{source.original_name}</span>
                  {source.token_estimate > 0 && (
                    <span className="text-xs text-muted-foreground shrink-0">
                      ~{(source.token_estimate / 1000).toFixed(1)}k tokens
                    </span>
                  )}
                  <Badge variant={source.status === "ready" ? "secondary" : "outline"} className="text-xs shrink-0">
                    {source.status}
                  </Badge>
                </div>
              ))}
            </div>

            {/* Summary */}
            <div className="border-t pt-3 mt-3 grid grid-cols-2 gap-4 text-center">
              <div>
                <p className="text-lg font-semibold">
                  {(prepareResult.total_token_estimate / 1000).toFixed(1)}k
                </p>
                <p className="text-xs text-muted-foreground">
                  Total tokens
                </p>
              </div>
              <div>
                <p className="text-lg font-semibold">
                  {prepareResult.image_count}
                </p>
                <p className="text-xs text-muted-foreground">Images</p>
              </div>
            </div>
          </div>

          {/* Public-cache opt-out — hidden when only files were uploaded */}
          {hasLinkSources && (
            <label className="flex items-start gap-3">
              <Switch
                checked={shareWithPublicGraph}
                onCheckedChange={setShareWithPublicGraph}
                size="sm"
                aria-label="Share with public knowledge graph"
              />
              <div className="flex-1">
                <p className="text-sm font-medium">Share with public graph</p>
                <p className="text-xs text-muted-foreground">
                  Contribute extracted facts to the shared public knowledge
                  pool so other graphs save decomposition cost on the same
                  URLs.
                </p>
              </div>
            </label>
          )}

          {/* Error display */}
          {error && (
            <div className="text-sm text-red-500 bg-red-50 dark:bg-red-950/20 rounded px-3 py-2">
              {error}
            </div>
          )}

          {/* Confirm / Cancel */}
          <div className="flex gap-3">
            <Button
              variant="outline"
              onClick={handleBack}
              className="flex-1"
              size="lg"
            >
              Cancel
            </Button>
            <Button
              onClick={handleExtractAndBuild}
              disabled={prepareResult.sources.length === 0}
              className="flex-1"
              size="lg"
            >
              Extract & Build ({prepareResult.sources.length} source
              {prepareResult.sources.length !== 1 ? "s" : ""})
            </Button>
          </div>
        </>
      )}

      {/* Step: Processing (decompose + auto-build with live progress) */}
      {step === "processing" && prepareResult && decomposeMessageId && (
        <ResearchBuildProgress
          conversationId={prepareResult.conversation_id}
          messageId={decomposeMessageId}
        />
      )}

      {/* Processing step — waiting for message ID */}
      {step === "processing" && !decomposeMessageId && (
        <div className="text-center py-12">
          <Loader2 className="size-8 mx-auto animate-spin text-primary mb-4" />
          <p className="text-sm text-muted-foreground">
            Starting extraction...
          </p>
        </div>
      )}
    </div>
  );
}
