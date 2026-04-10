"use client";

import { useState, useCallback, useRef, useMemo, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  Upload,
  X,
  FileText,
  Link2,
  Loader2,
  ChevronLeft,
  ChevronDown,
  ChevronRight,
  Check,
  Minus,
  Plus,
  GitBranch,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { AgentSelectDialog } from "@/components/research/AgentSelectDialog";
import type {
  IngestPrepareResponse,
  ChunkInfoResponse,
  BottomUpProposedNode,
  IngestProposalsResponse,
} from "@/types";

const ACCEPTED_TYPES = ".pdf,.txt,.png,.jpg,.jpeg,.webp";
const MAX_FILE_SIZE_MB = 50;

type Step =
  | "upload"
  | "preparing"
  | "confirm"
  | "decomposing"
  | "review"
  | "building";
type ChunkMode = "all" | "recommended" | "custom";

interface UploadedFile {
  file: File;
  id: string;
}

const NODE_TYPES = ["concept", "entity", "event"];
const NODE_TYPE_COLORS: Record<string, string> = {
  concept: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  entity:
    "bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300",
  event:
    "bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300",
};

function priorityColor(p: number): string {
  if (p >= 8) return "text-green-600 dark:text-green-400";
  if (p >= 5) return "text-yellow-600 dark:text-yellow-400";
  return "text-muted-foreground";
}

export function SourceUploadForm() {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState<Step>("upload");
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [links, setLinks] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [prepareResult, setPrepareResult] =
    useState<IngestPrepareResponse | null>(null);

  // Chunk selection state
  const [chunkMode, setChunkMode] = useState<ChunkMode>("recommended");
  const [selectedChunks, setSelectedChunks] = useState<Set<number>>(new Set());
  const [expandedSources, setExpandedSources] = useState<Set<string>>(
    new Set(),
  );

  // Decompose state
  const [decomposeMessageId, setDecomposeMessageId] = useState<string | null>(
    null,
  );

  // Multigraph public-cache opt-out (PR8). Defaults to "share with public
  // graph" since that's how the public pool grows. The toggle is hidden
  // in the file-only case because the API forces False server-side
  // there anyway — file uploads can never participate.
  const [shareWithPublicGraph, setShareWithPublicGraph] = useState(true);

  // Review state (proposals)
  const [proposals, setProposals] = useState<IngestProposalsResponse | null>(
    null,
  );
  const [nodes, setNodes] = useState<BottomUpProposedNode[]>([]);
  const [customNodeName, setCustomNodeName] = useState("");

  // Initialize chunk selection when prepare result arrives
  const initChunkSelection = useCallback(
    (result: IngestPrepareResponse) => {
      const recommended = new Set(
        result.chunks.filter((c) => c.recommended).map((c) => c.chunk_index),
      );
      setSelectedChunks(recommended);
      setChunkMode("recommended");
    },
    [],
  );

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

  // Group chunks by source for display
  const chunksBySource = useMemo(() => {
    if (!prepareResult) return new Map<string, ChunkInfoResponse[]>();
    const map = new Map<string, ChunkInfoResponse[]>();
    for (const c of prepareResult.chunks) {
      const existing = map.get(c.source_id) ?? [];
      existing.push(c);
      map.set(c.source_id, existing);
    }
    return map;
  }, [prepareResult]);

  const selectedChunkCount = selectedChunks.size;

  // Whether the prepared ingest has any link sources at all. Drives the
  // visibility of the public-cache opt-out toggle — file-only ingests
  // hide it because the API forces ``share_with_public_graph=false``
  // server-side regardless of the client value.
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
      initChunkSelection(result);
      setStep("confirm");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed");
      setStep("upload");
    }
  };

  // Chunk selection helpers
  const selectAll = useCallback(() => {
    if (!prepareResult) return;
    setSelectedChunks(new Set(prepareResult.chunks.map((c) => c.chunk_index)));
    setChunkMode("all");
  }, [prepareResult]);

  const selectRecommended = useCallback(() => {
    if (!prepareResult) return;
    setSelectedChunks(
      new Set(
        prepareResult.chunks
          .filter((c) => c.recommended)
          .map((c) => c.chunk_index),
      ),
    );
    setChunkMode("recommended");
  }, [prepareResult]);

  const toggleChunk = useCallback((index: number) => {
    setSelectedChunks((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
    setChunkMode("custom");
  }, []);

  const toggleSource = useCallback(
    (sourceId: string) => {
      const sourceChunks = chunksBySource.get(sourceId) ?? [];
      const allSelected = sourceChunks.every((c) =>
        selectedChunks.has(c.chunk_index),
      );
      setSelectedChunks((prev) => {
        const next = new Set(prev);
        for (const c of sourceChunks) {
          if (allSelected) {
            next.delete(c.chunk_index);
          } else {
            next.add(c.chunk_index);
          }
        }
        return next;
      });
      setChunkMode("custom");
    },
    [chunksBySource, selectedChunks],
  );

  const toggleExpand = useCallback((sourceId: string) => {
    setExpandedSources((prev) => {
      const next = new Set(prev);
      if (next.has(sourceId)) {
        next.delete(sourceId);
      } else {
        next.add(sourceId);
      }
      return next;
    });
  }, []);

  // Step 2: Decompose (Phase 1)
  const handleDecompose = async () => {
    if (!prepareResult) return;

    setStep("decomposing");
    setError(null);

    try {
      const allSelected =
        selectedChunkCount === prepareResult.chunks.length
          ? null
          : Array.from(selectedChunks);

      const result = await api.research.decompose(
        prepareResult.conversation_id,
        allSelected,
        shareWithPublicGraph,
      );
      setDecomposeMessageId(result.message_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Decompose failed");
      setStep("confirm");
    }
  };

  // Poll for decompose completion
  useEffect(() => {
    if (step !== "decomposing" || !prepareResult || !decomposeMessageId) return;

    const interval = setInterval(async () => {
      try {
        const progress = await api.conversations.getProgress(
          prepareResult.conversation_id,
          decomposeMessageId,
        );
        if (progress.status === "completed") {
          clearInterval(interval);
          // Fetch proposals
          const proposalData = await api.research.proposals(
            prepareResult.conversation_id,
          );
          setProposals(proposalData);
          setNodes(
            proposalData.proposed_nodes.map((n) => ({ ...n })),
          );
          setStep("review");
        } else if (progress.status === "failed") {
          clearInterval(interval);
          setError(progress.error || "Decomposition failed");
          setStep("confirm");
        }
      } catch {
        // Keep polling on transient errors
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [step, prepareResult, decomposeMessageId]);

  // Node editing helpers (review step)
  const toggleNode = useCallback((idx: number) => {
    setNodes((prev) =>
      prev.map((n, i) => (i === idx ? { ...n, selected: !n.selected } : n)),
    );
  }, []);

  const editNodeName = useCallback((idx: number, name: string) => {
    setNodes((prev) =>
      prev.map((n, i) => (i === idx ? { ...n, name } : n)),
    );
  }, []);

  const editNodeType = useCallback((idx: number, node_type: string) => {
    setNodes((prev) =>
      prev.map((n, i) => (i === idx ? { ...n, node_type } : n)),
    );
  }, []);


  const selectAllNodes = useCallback(() => {
    setNodes((prev) => prev.map((n) => ({ ...n, selected: true })));
  }, []);

  const selectNoneNodes = useCallback(() => {
    setNodes((prev) => prev.map((n) => ({ ...n, selected: false })));
  }, []);

  const addCustomNode = useCallback(() => {
    if (!customNodeName.trim()) return;
    setNodes((prev) => [
      {
        name: customNodeName.trim(),
        node_type: "concept",
        entity_subtype: null,
        priority: 10,
        selected: true,
        seed_key: "",
        existing_node_id: null,
        fact_count: 0,
        aliases: [],
        perspectives: [],
        ambiguity: null,
      },
      ...prev,
    ]);
    setCustomNodeName("");
  }, [customNodeName]);

  const selectedNodeCount = nodes.filter((n) => n.selected).length;

  // Step 3: Build (Phase 2)
  const handleBuild = async () => {
    if (!prepareResult) return;

    setStep("building");
    setError(null);

    try {
      const selected = nodes.filter((n) => n.selected);
      await api.research.build(prepareResult.conversation_id, selected);
      router.push(`/grow-graph`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Build failed");
      setStep("review");
    }
  };

  // Legacy confirm (autonomous agent path)
  const handleLegacyConfirm = async () => {
    if (!prepareResult) return;

    setStep("building");
    setError(null);

    try {
      const allSelected =
        selectedChunkCount === prepareResult.chunks.length
          ? null
          : Array.from(selectedChunks);

      await api.research.confirm(
        prepareResult.conversation_id,
        50, // default nav budget for legacy path
        allSelected,
        shareWithPublicGraph,
      );
      router.push(`/grow-graph`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ingest failed");
      setStep("confirm");
    }
  };

  const handleBack = () => {
    if (step === "review") {
      setStep("confirm");
      setProposals(null);
      setNodes([]);
      return;
    }
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

          {/* Chunk selection mode toggle */}
          <div className="border rounded-lg p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-medium">
                Chunks to process ({selectedChunkCount} of{" "}
                {prepareResult.chunks.length})
              </h4>
              <div className="flex gap-1.5">
                <Button
                  variant={chunkMode === "all" ? "default" : "outline"}
                  size="sm"
                  onClick={selectAll}
                  className="text-xs h-7"
                >
                  All ({prepareResult.chunks.length})
                </Button>
                <Button
                  variant={chunkMode === "recommended" ? "default" : "outline"}
                  size="sm"
                  onClick={selectRecommended}
                  className="text-xs h-7"
                >
                  Recommended ({prepareResult.recommended_chunks})
                </Button>
              </div>
            </div>

            {/* Per-source chunk list */}
            <div className="space-y-1 max-h-80 overflow-y-auto">
              {Array.from(chunksBySource.entries()).map(
                ([sourceId, sourceChunks]) => {
                  const sourceName = sourceChunks[0]?.source_name ?? sourceId;
                  const isExpanded = expandedSources.has(sourceId);
                  const selectedInSource = sourceChunks.filter((c) =>
                    selectedChunks.has(c.chunk_index),
                  ).length;
                  const allInSourceSelected =
                    selectedInSource === sourceChunks.length;
                  const someInSourceSelected =
                    selectedInSource > 0 && !allInSourceSelected;

                  return (
                    <div key={sourceId} className="border rounded">
                      {/* Source header */}
                      <button
                        type="button"
                        className="flex items-center gap-2 w-full px-3 py-2 text-sm hover:bg-muted/50 transition-colors"
                        onClick={() => toggleExpand(sourceId)}
                      >
                        {isExpanded ? (
                          <ChevronDown className="size-3.5 text-muted-foreground shrink-0" />
                        ) : (
                          <ChevronRight className="size-3.5 text-muted-foreground shrink-0" />
                        )}
                        <button
                          type="button"
                          className={`size-4 shrink-0 rounded border flex items-center justify-center transition-colors ${
                            allInSourceSelected
                              ? "bg-primary border-primary text-primary-foreground"
                              : someInSourceSelected
                                ? "bg-primary/30 border-primary"
                                : "border-muted-foreground/30"
                          }`}
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleSource(sourceId);
                          }}
                        >
                          {allInSourceSelected ? (
                            <Check className="size-3" />
                          ) : someInSourceSelected ? (
                            <Minus className="size-3" />
                          ) : null}
                        </button>
                        <span className="truncate flex-1 text-left">
                          {sourceName}
                        </span>
                        <span className="text-xs text-muted-foreground shrink-0">
                          {selectedInSource}/{sourceChunks.length}
                        </span>
                      </button>

                      {/* Chunk rows */}
                      {isExpanded && (
                        <div className="border-t">
                          {sourceChunks.map((chunk) => {
                            const isSelected = selectedChunks.has(
                              chunk.chunk_index,
                            );
                            return (
                              <button
                                type="button"
                                key={chunk.chunk_index}
                                className="flex items-start gap-2 w-full px-3 py-1.5 text-xs hover:bg-muted/30 transition-colors"
                                onClick={() => toggleChunk(chunk.chunk_index)}
                              >
                                <span
                                  className={`size-3.5 shrink-0 mt-0.5 rounded border flex items-center justify-center transition-colors ${
                                    isSelected
                                      ? "bg-primary border-primary text-primary-foreground"
                                      : "border-muted-foreground/30"
                                  }`}
                                >
                                  {isSelected && (
                                    <Check className="size-2.5" />
                                  )}
                                </span>
                                <span className="text-muted-foreground shrink-0 w-6 text-right">
                                  #{chunk.chunk_index}
                                </span>
                                <span className="flex-1 text-left truncate">
                                  {chunk.preview}
                                </span>
                                {!chunk.recommended && chunk.reason && (
                                  <span className="text-amber-600 dark:text-amber-400 shrink-0 italic">
                                    {chunk.reason}
                                  </span>
                                )}
                                <span className="text-muted-foreground shrink-0">
                                  {chunk.is_image
                                    ? "img"
                                    : `${(chunk.char_count / 1024).toFixed(0)}k`}
                                </span>
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                },
              )}
            </div>

            {/* Summary */}
            <div className="border-t pt-3 mt-3 grid grid-cols-3 gap-4 text-center">
              <div>
                <p className="text-lg font-semibold">{selectedChunkCount}</p>
                <p className="text-xs text-muted-foreground">
                  Chunks selected
                </p>
              </div>
              <div>
                <p className="text-lg font-semibold">
                  {prepareResult.image_count}
                </p>
                <p className="text-xs text-muted-foreground">Images</p>
              </div>
              <div>
                <p className="text-lg font-semibold">{selectedChunkCount}</p>
                <p className="text-xs text-muted-foreground">
                  Decompose calls
                </p>
              </div>
            </div>
          </div>

          {/* Public-cache opt-out — hidden when only files were uploaded
              (the API forces share=false server-side in that case so the
              switch would be a misleading affordance). */}
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
              onClick={handleDecompose}
              disabled={selectedChunkCount === 0}
              className="flex-1"
              size="lg"
            >
              Extract & Analyze ({selectedChunkCount} chunk
              {selectedChunkCount !== 1 ? "s" : ""})
            </Button>
          </div>
          <button
            type="button"
            onClick={handleLegacyConfirm}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors w-full text-center"
          >
            Or use autonomous agent mode (skip node review)
          </button>
        </>
      )}

      {/* Step: Decomposing */}
      {step === "decomposing" && (
        <div className="text-center py-12">
          <Loader2 className="size-8 mx-auto animate-spin text-primary mb-4" />
          <p className="text-sm font-medium mb-1">
            Extracting facts and identifying entities...
          </p>
          <p className="text-xs text-muted-foreground">
            This may take a few minutes for large documents.
          </p>
        </div>
      )}

      {/* Step: Review proposals */}
      {step === "review" && proposals && (
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
            <h3 className="font-medium">Review Proposed Nodes</h3>
          </div>

          {/* Summary header */}
          <div className="border rounded-lg p-4 space-y-2 bg-muted/30">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">
                {proposals.fact_count} facts extracted
              </span>
              <span className="text-sm text-muted-foreground">
                {nodes.length} nodes proposed
              </span>
            </div>
            {proposals.key_topics.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {proposals.key_topics.slice(0, 10).map((topic) => (
                  <Badge key={topic} variant="secondary" className="text-[10px]">
                    {topic}
                  </Badge>
                ))}
              </div>
            )}
            {proposals.content_summary && (
              <p className="text-xs text-muted-foreground">
                {proposals.content_summary}
              </p>
            )}
          </div>

          {/* Selection controls */}
          <div className="flex items-center justify-between">
            <div className="flex gap-1.5">
              {prepareResult && (
                <AgentSelectDialog
                  conversationId={prepareResult.conversation_id}
                  totalNodes={nodes.length}
                  defaultContext={
                    files.length > 0
                      ? files.map((f) => f.file.name).join(", ")
                      : links.split("\n").filter((l) => l.trim()).join(", ")
                  }
                  mode="ingest"
                  onComplete={(updated) => setNodes(updated)}
                />
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={selectAllNodes}
                className="text-xs h-7"
              >
                All
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={selectNoneNodes}
                className="text-xs h-7"
              >
                None
              </Button>
            </div>
            <span className="text-sm text-muted-foreground">
              {selectedNodeCount} of {nodes.length} selected
            </span>
          </div>

          {/* Add custom node */}
          <div className="flex gap-2">
            <Input
              placeholder="Add custom node..."
              value={customNodeName}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                setCustomNodeName(e.target.value)
              }
              onKeyDown={(e: React.KeyboardEvent) => {
                if (e.key === "Enter") addCustomNode();
              }}
              className="text-sm"
            />
            <Button
              variant="outline"
              size="sm"
              onClick={addCustomNode}
              disabled={!customNodeName.trim()}
            >
              <Plus className="size-4" />
            </Button>
          </div>

          {/* Node list */}
          <div className="space-y-1 max-h-[28rem] overflow-y-auto">
            {nodes.map((node, idx) => {
              return (
                <div
                  key={idx}
                  className={`border rounded transition-colors ${
                    node.selected
                      ? "border-primary/30 bg-primary/5"
                      : "opacity-60"
                  }`}
                >
                  {/* Node row */}
                  <div className="flex items-center gap-2 px-3 py-2">
                    {/* Checkbox */}
                    <button
                      type="button"
                      className={`size-4 shrink-0 rounded border flex items-center justify-center transition-colors ${
                        node.selected
                          ? "bg-primary border-primary text-primary-foreground"
                          : "border-muted-foreground/30"
                      }`}
                      onClick={() => toggleNode(idx)}
                    >
                      {node.selected && <Check className="size-3" />}
                    </button>

                    {/* Priority */}
                    <span
                      className={`text-xs font-mono w-4 text-center shrink-0 ${priorityColor(node.priority)}`}
                    >
                      {node.priority}
                    </span>

                    {/* Editable name */}
                    <Input
                      value={node.name}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                        editNodeName(idx, e.target.value)
                      }
                      className="h-7 text-sm flex-1 border-transparent hover:border-input focus:border-input bg-transparent"
                    />

                    {/* Type dropdown */}
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button type="button" className="shrink-0">
                          <Badge
                            variant="secondary"
                            className={`text-[10px] cursor-pointer hover:opacity-80 ${NODE_TYPE_COLORS[node.node_type] || ""}`}
                          >
                            {node.node_type}
                          </Badge>
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        {NODE_TYPES.map((t) => (
                          <DropdownMenuItem
                            key={t}
                            onClick={() => editNodeType(idx, t)}
                            className="text-xs"
                          >
                            <span
                              className={`inline-block w-2 h-2 rounded-full mr-2 ${NODE_TYPE_COLORS[t]?.split(" ")[0] || ""}`}
                            />
                            {t}
                            {t === node.node_type && (
                              <Check className="size-3 ml-auto" />
                            )}
                          </DropdownMenuItem>
                        ))}
                      </DropdownMenuContent>
                    </DropdownMenu>

                      {/* Ambiguity indicator */}
                      {node.ambiguity?.is_disambiguated && (
                        <Badge
                          variant="secondary"
                          className="text-[10px] bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200 gap-0.5 shrink-0"
                          title={
                            node.ambiguity.parent_name
                              ? `Disambiguated from '${node.ambiguity.parent_name}'`
                              : "Disambiguated seed"
                          }
                        >
                          <GitBranch className="size-2.5" />
                        </Badge>
                      )}
                  </div>

                    {/* Merged aliases & disambiguation details */}
                    {(node.aliases.length > 0 || node.ambiguity?.is_disambiguated) && (
                      <div className="border-t px-3 py-1.5 flex flex-wrap gap-1.5 items-center text-[10px] text-muted-foreground">
                        {node.aliases.length > 0 && (
                          <>
                            <span className="font-medium">Merged:</span>
                            {node.aliases.map((alias, ai) => (
                              <Badge key={ai} variant="outline" className="text-[10px] py-0 h-4">
                                {alias}
                              </Badge>
                            ))}
                          </>
                        )}
                        {node.ambiguity?.is_disambiguated && node.ambiguity.parent_name && (
                          <>
                            {node.aliases.length > 0 && <span className="text-muted-foreground/40">|</span>}
                            <span className="font-medium">Split from:</span>
                            <span className="italic">{node.ambiguity.parent_name}</span>
                            {node.ambiguity.sibling_names.length > 0 && (
                              <>
                                <span className="font-medium ml-1">siblings:</span>
                                {node.ambiguity.sibling_names.map((s, si) => (
                                  <Badge key={si} variant="outline" className="text-[10px] py-0 h-4 border-purple-300 dark:border-purple-700">
                                    {s}
                                  </Badge>
                                ))}
                              </>
                            )}
                          </>
                        )}
                      </div>
                    )}
                </div>
              );
            })}
          </div>

          {/* Error display */}
          {error && (
            <div className="text-sm text-red-500 bg-red-50 dark:bg-red-950/20 rounded px-3 py-2">
              {error}
            </div>
          )}

          {/* Build button */}
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
              onClick={handleBuild}
              disabled={selectedNodeCount === 0}
              className="flex-1"
              size="lg"
            >
              Confirm & Build ({selectedNodeCount} node
              {selectedNodeCount !== 1 ? "s" : ""})
            </Button>
          </div>
        </>
      )}

      {/* Step: Building */}
      {step === "building" && (
        <div className="text-center py-12">
          <Loader2 className="size-8 mx-auto animate-spin text-primary mb-4" />
          <p className="text-sm text-muted-foreground">
            Building nodes... you will be redirected shortly.
          </p>
        </div>
      )}
    </div>
  );
}
