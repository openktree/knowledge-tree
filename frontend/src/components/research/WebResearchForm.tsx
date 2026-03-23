"use client";

import { useState, useCallback, useEffect } from "react";
import {
  Loader2,
  ChevronDown,
  ChevronRight,
  Search,
  Hammer,
  ExternalLink,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useAuth } from "@/contexts/auth";
import type { ResearchSummaryResponse } from "@/types";

type Step = "configure" | "gathering" | "summary";

const EXPLORE_PRESETS = [
  { label: "Quick", value: 5 },
  { label: "Standard", value: 20 },
  { label: "Deep", value: 50 },
  { label: "Exhaustive", value: 150 },
];

const NODE_TYPE_COLORS: Record<string, string> = {
  concept: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
  entity: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
  event: "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-200",
  perspective:
    "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
};

interface WebResearchFormProps {
  /** If set, skip Phase 1 and resume directly into summary. */
  resumeConversationId?: string | null;
  /** Called when the form resets, so parent can clear resumeId. */
  onResetResume?: () => void;
  /** Called when user wants to build from seeds. */
  onBuildFromSeeds?: (query: string, seedKeys: string[]) => void;
}

export function WebResearchForm({
  resumeConversationId,
  onResetResume,
  onBuildFromSeeds,
}: WebResearchFormProps = {}) {
  const { user } = useAuth();
  const [step, setStep] = useState<Step>("configure");
  const [query, setQuery] = useState("");
  const [exploreBudget, setExploreBudget] = useState(20);
  const [error, setError] = useState<string | null>(null);

  // Result
  const [, setConversationId] = useState<string | null>(null);
  const [summary, setSummary] = useState<ResearchSummaryResponse | null>(null);
  const [showSources, setShowSources] = useState(false);

  // Polling state
  const [pollTimer, setPollTimer] = useState<ReturnType<
    typeof setInterval
  > | null>(null);

  // ── Resume from history ─────────────────────────────────────────────
  useEffect(() => {
    if (!resumeConversationId) return;
    let cancelled = false;

    async function loadSummary() {
      try {
        const result = await api.research.getSummary(resumeConversationId!);
        if (cancelled) return;
        setConversationId(resumeConversationId!);
        setSummary(result);
        setStep("summary");
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load summary — research may not be complete",
        );
        setStep("configure");
        onResetResume?.();
      }
    }

    loadSummary();
    return () => {
      cancelled = true;
    };
  }, [resumeConversationId]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Step 1: Start gathering ─────────────────────────────────────────

  const handleStartGathering = async () => {
    if (!query.trim()) return;
    setStep("gathering");
    setError(null);

    if (user?.has_api_key) {
      toast.info("This research will use your OpenRouter API key. Costs depend on query complexity.");
    } else if (user?.is_superuser) {
      toast.info("Using system API key for this research.");
    }

    try {
      const response = await api.research.bottomUpPrepare({
        query: query.trim(),
        explore_budget: exploreBudget,
      });
      setConversationId(response.id);

      // Poll for completion
      const timer = setInterval(async () => {
        try {
          const progress = await api.conversations.getProgress(
            response.id,
            response.messages[1]?.id || "",
          );
          if (progress.status === "completed") {
            clearInterval(timer);
            setPollTimer(null);
            // Fetch summary
            const result = await api.research.getSummary(response.id);
            setSummary(result);
            setStep("summary");
          } else if (progress.status === "failed") {
            clearInterval(timer);
            setPollTimer(null);
            setError(progress.error || "Fact gathering failed");
            setStep("configure");
          }
        } catch {
          // Ignore transient errors during polling
        }
      }, 3000);
      setPollTimer(timer);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to start gathering",
      );
      setStep("configure");
    }
  };

  const cancelGathering = useCallback(() => {
    if (pollTimer) {
      clearInterval(pollTimer);
      setPollTimer(null);
    }
    setStep("configure");
  }, [pollTimer]);

  const handleNewResearch = useCallback(() => {
    setStep("configure");
    setQuery("");
    setConversationId(null);
    setSummary(null);
    setError(null);
    setShowSources(false);
  }, []);

  const handleBuildSeeds = useCallback(() => {
    if (!summary) return;
    const seedKeys = summary.seeds.map((s) => s.key);
    if (onBuildFromSeeds) {
      onBuildFromSeeds(query || "", seedKeys);
    }
  }, [summary, query, onBuildFromSeeds]);

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Step: Configure */}
      {step === "configure" && (
        <>
          <div>
            <Label className="text-sm font-medium">Research Query</Label>
            <Textarea
              placeholder={
                'What topic should we research from the web?\ne.g., "Recent advances in quantum error correction"'
              }
              value={query}
              onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                setQuery(e.target.value)
              }
              rows={3}
              className="mt-2"
            />
          </div>

          <div>
            <Label className="text-sm font-medium">Search Depth</Label>
            <p className="text-xs text-muted-foreground mb-2">
              How many web searches to perform for fact gathering.
            </p>
            <div className="flex gap-1.5 mb-3 items-center">
              {EXPLORE_PRESETS.map((p) => (
                <Button
                  key={p.value}
                  variant={exploreBudget === p.value ? "default" : "outline"}
                  size="sm"
                  onClick={() => setExploreBudget(p.value)}
                  className="text-xs"
                >
                  {p.label} ({p.value})
                </Button>
              ))}
              <Input
                type="number"
                value={exploreBudget}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                  const v = Number(e.target.value);
                  if (v >= 1) setExploreBudget(v);
                }}
                min={1}
                className="h-8 w-20 text-sm text-center font-mono ml-auto"
                title="Custom budget"
              />
            </div>
            <Slider
              value={[Math.min(exploreBudget, 500)]}
              onValueChange={([v]) => setExploreBudget(v)}
              min={1}
              max={500}
              step={1}
            />
          </div>

          {error && (
            <div className="text-sm text-red-500 bg-red-50 dark:bg-red-950/20 rounded px-3 py-2">
              {error}
            </div>
          )}

          <Button
            onClick={handleStartGathering}
            disabled={!query.trim()}
            className="w-full"
            size="lg"
          >
            <Search className="size-4 mr-2" />
            Start Research
          </Button>
        </>
      )}

      {/* Step: Gathering (polling) */}
      {step === "gathering" && (
        <div className="text-center py-12 space-y-4">
          <Loader2 className="size-8 mx-auto animate-spin text-primary" />
          <div>
            <p className="font-medium">Gathering facts from the web...</p>
            <p className="text-sm text-muted-foreground mt-1">
              Scouting the knowledge graph, searching sources, and extracting
              facts. This may take a few minutes.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={cancelGathering}>
            Cancel
          </Button>
        </div>
      )}

      {/* Step: Summary — research complete */}
      {step === "summary" && summary && (
        <>
          {/* Stats */}
          <div className="border rounded-lg p-4 space-y-2 bg-muted/30">
            <h4 className="text-sm font-medium">Research Complete</h4>
            <div className="grid grid-cols-3 gap-4 text-center">
              <div>
                <p className="text-lg font-semibold">{summary.fact_count}</p>
                <p className="text-xs text-muted-foreground">Facts gathered</p>
              </div>
              <div>
                <p className="text-lg font-semibold">{summary.source_count}</p>
                <p className="text-xs text-muted-foreground">
                  Sources processed
                </p>
              </div>
              <div>
                <p className="text-lg font-semibold">{summary.seeds.length}</p>
                <p className="text-xs text-muted-foreground">Seeds created</p>
              </div>
            </div>
            {summary.content_summary && (
              <p className="text-xs text-muted-foreground mt-2 line-clamp-3">
                {summary.content_summary}
              </p>
            )}
          </div>

          {/* Seeds list */}
          {summary.seeds.length > 0 && (
            <div className="border rounded-lg p-4 space-y-3">
              <h4 className="text-sm font-medium">
                Seeds ({summary.seeds.length})
              </h4>
              <div className="space-y-1 max-h-[28rem] overflow-y-auto">
                {summary.seeds.map((seed) => (
                  <div
                    key={seed.key}
                    className="border rounded px-3 py-2 flex items-center gap-2"
                  >
                    <span className="text-sm flex-1 min-w-0 truncate">
                      {seed.name}
                    </span>
                    <Badge
                      variant="secondary"
                      className={`text-[10px] shrink-0 ${NODE_TYPE_COLORS[seed.node_type] || ""}`}
                    >
                      {seed.node_type}
                    </Badge>
                    {seed.fact_count > 0 && (
                      <span className="text-xs text-muted-foreground tabular-nums shrink-0">
                        {seed.fact_count} fact{seed.fact_count !== 1 ? "s" : ""}
                      </span>
                    )}
                    {seed.status === "promoted" && (
                      <Badge
                        variant="outline"
                        className="text-[10px] shrink-0 border-green-300 text-green-700 dark:border-green-700 dark:text-green-400"
                      >
                        promoted
                      </Badge>
                    )}
                    {seed.aliases.length > 0 && (
                      <span
                        className="text-[10px] text-muted-foreground shrink-0"
                        title={`Aliases: ${seed.aliases.join(", ")}`}
                      >
                        +{seed.aliases.length}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Source URLs (collapsible) */}
          {summary.source_urls && summary.source_urls.length > 0 && (
            <div className="border rounded-lg overflow-hidden">
              <button
                type="button"
                className="w-full flex items-center gap-2 px-4 py-2.5 text-xs text-muted-foreground hover:bg-muted/30 transition-colors"
                onClick={() => setShowSources((s) => !s)}
              >
                {showSources ? (
                  <ChevronDown className="size-3.5" />
                ) : (
                  <ChevronRight className="size-3.5" />
                )}
                Sources ({summary.source_urls.length})
              </button>
              {showSources && (
                <div className="border-t px-4 py-2 space-y-1 max-h-48 overflow-y-auto">
                  {summary.source_urls.map((src, i) => (
                    <a
                      key={i}
                      href={src.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
                    >
                      <ExternalLink className="size-3 shrink-0" />
                      <span className="truncate">
                        {src.title || src.url}
                      </span>
                    </a>
                  ))}
                </div>
              )}
            </div>
          )}

          {error && (
            <div className="text-sm text-red-500 bg-red-50 dark:bg-red-950/20 rounded px-3 py-2">
              {error}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2">
            <Button variant="outline" onClick={handleNewResearch} className="flex-1">
              New Research
            </Button>
            {summary.seeds.length > 0 && (
              <Button onClick={handleBuildSeeds} className="flex-1 gap-2">
                <Hammer className="size-4" />
                Build Graph from Seeds
              </Button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
