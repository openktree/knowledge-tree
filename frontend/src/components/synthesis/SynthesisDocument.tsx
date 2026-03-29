"use client";

import { useState, useMemo, useCallback } from "react";
import Link from "next/link";
import Markdown from "react-markdown";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  ChevronRight,
  CircleDot,
  ExternalLink,
  Loader2,
} from "lucide-react";
import { formatSynthesisConcept } from "./utils";
import type {
  SynthesisDocumentResponse,
  SynthesisSentenceResponse,
  SynthesisNodeResponse,
  SentenceFactLink,
} from "@/types";
import { getSentenceFacts, updateSynthesisVisibility } from "@/lib/api";
import { SynthesisNodeList } from "./SynthesisNodeList";
import { SubSynthesisList } from "./SubSynthesisList";

// ── Types ────────────────────────────────────────────────────────

interface SynthesisDocumentProps {
  document: SynthesisDocumentResponse;
  onRegenerate?: () => void;
  isRegenerating?: boolean;
}

interface FactGroup {
  key: string;
  title: string;
  author: string;
  sourceUri: string;
  bestDistance: number;
  facts: SentenceFactLink[];
}

// ── Helpers ──────────────────────────────────────────────────────

function buildParagraphSentenceMap(
  definition: string,
  sentences: SynthesisSentenceResponse[]
): Map<string, SynthesisSentenceResponse[]> {
  const map = new Map<string, SynthesisSentenceResponse[]>();
  if (!definition || sentences.length === 0) return map;

  const rawBlocks = definition
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);

  const paragraphs: string[] = [];
  for (const block of rawBlocks) {
    const lines = block.split("\n").map((l) => l.trim()).filter(Boolean);
    const isList = lines.length > 1 && lines.every((l) => /^(\d+\.\s|[-*]\s)/.test(l));
    if (isList) {
      for (const line of lines) paragraphs.push(line);
    } else {
      paragraphs.push(block);
    }
  }

  for (const para of paragraphs) {
    const plainPara = para
      .replace(/^#{1,6}\s+/, "")
      .replace(/^\d+\.\s+/, "")
      .replace(/^[-*]\s+/, "")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/\*([^*]+)\*/g, "$1");

    const matched: SynthesisSentenceResponse[] = [];
    for (const s of sentences) {
      const plainSentence = s.text
        .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
        .replace(/\*\*([^*]+)\*\*/g, "$1")
        .replace(/\*([^*]+)\*/g, "$1")
        .trim();
      if (plainSentence.length < 10) continue;
      const sentStart = plainSentence.slice(0, 40);
      const sentMid =
        plainSentence.length > 50 ? plainSentence.slice(10, 50) : "";
      if (
        plainPara.includes(sentStart) ||
        (sentMid && plainPara.includes(sentMid)) ||
        plainSentence.includes(plainPara.slice(0, 40))
      ) {
        matched.push(s);
      }
    }

    if (matched.length > 0) {
      map.set(plainPara.slice(0, 60).trim(), matched);
    }
  }
  return map;
}

function extractText(children: React.ReactNode): string {
  if (!children) return "";
  if (typeof children === "string") return children;
  if (typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(extractText).join("");
  if (typeof children === "object" && "props" in children) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return extractText((children as any).props?.children);
  }
  return "";
}

function groupFactsBySource(facts: SentenceFactLink[]): FactGroup[] {
  const groups = new Map<string, FactGroup>();
  for (const fl of facts) {
    const key = fl.source_title || fl.source_uri || "Unknown source";
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        title: fl.source_title,
        author: fl.author,
        sourceUri: fl.source_uri,
        bestDistance: fl.embedding_distance,
        facts: [],
      });
    }
    const group = groups.get(key)!;
    group.facts.push(fl);
    if (fl.embedding_distance > group.bestDistance) {
      group.bestDistance = fl.embedding_distance;
    }
  }
  for (const group of groups.values()) {
    group.facts.sort((a, b) => b.embedding_distance - a.embedding_distance);
  }
  return Array.from(groups.values()).sort(
    (a, b) => b.bestDistance - a.bestDistance
  );
}

// ── Main Component ───────────────────────────────────────────────

export function SynthesisDocument({ document, onRegenerate, isRegenerating }: SynthesisDocumentProps) {
  const [selectedParaKey, setSelectedParaKey] = useState<string | null>(null);
  const [selectedParaText, setSelectedParaText] = useState<string>("");
  const [selectedNodes, setSelectedNodes] = useState<string[]>([]);
  const [factLinks, setFactLinks] = useState<SentenceFactLink[]>([]);
  const [loadingFacts, setLoadingFacts] = useState(false);
  const [nodesOpen, setNodesOpen] = useState(true);
  const [factsOpen, setFactsOpen] = useState(true);
  const [visibility, setVisibility] = useState(document.visibility);
  const [togglingVisibility, setTogglingVisibility] = useState(false);

  const dialogOpen = selectedParaKey !== null;
  const { title, date } = formatSynthesisConcept(document.concept);

  const nodeMap = useMemo(() => {
    const map = new Map<string, SynthesisNodeResponse>();
    for (const n of document.referenced_nodes) {
      map.set(n.node_id, n);
    }
    return map;
  }, [document.referenced_nodes]);

  const paragraphMap = useMemo(
    () =>
      buildParagraphSentenceMap(
        document.definition || "",
        document.sentences
      ),
    [document.definition, document.sentences]
  );

  const handleSectionClick = useCallback(
    async (
      key: string,
      sentences: SynthesisSentenceResponse[],
      paraText: string
    ) => {
      setSelectedParaKey(key);
      setSelectedParaText(paraText);
      setNodesOpen(true);
      setFactsOpen(true);

      const nodeIds = new Set<string>();
      for (const s of sentences) {
        for (const nid of s.node_ids) nodeIds.add(nid);
      }
      setSelectedNodes(Array.from(nodeIds));

      const sentencesWithFacts = sentences.filter((s) => s.fact_count > 0);
      if (sentencesWithFacts.length > 0) {
        setLoadingFacts(true);
        setFactLinks([]);
        try {
          const allFacts: SentenceFactLink[] = [];
          const seen = new Set<string>();
          for (const s of sentencesWithFacts) {
            const facts = await getSentenceFacts(document.id, s.position);
            for (const f of facts) {
              if (!seen.has(f.fact_id)) {
                seen.add(f.fact_id);
                allFacts.push(f);
              }
            }
          }
          setFactLinks(allFacts);
        } catch {
          setFactLinks([]);
        } finally {
          setLoadingFacts(false);
        }
      } else {
        setFactLinks([]);
      }
    },
    [document.id]
  );

  const toggleVisibility = useCallback(async () => {
    const newVisibility = visibility === "public" ? "private" : "public";
    setTogglingVisibility(true);
    try {
      await updateSynthesisVisibility(document.id, newVisibility);
      setVisibility(newVisibility);
    } catch {
      // revert on error
    } finally {
      setTogglingVisibility(false);
    }
  }, [visibility, document.id]);

  const closeDialog = () => {
    setSelectedParaKey(null);
    setSelectedParaText("");
    setSelectedNodes([]);
    setFactLinks([]);
  };

  const getSectionInfo = useCallback(
    (children: React.ReactNode) => {
      const text = extractText(children);
      const key = text.slice(0, 60).trim();
      const sentences = paragraphMap.get(key);
      const hasInfo =
        sentences &&
        sentences.some((s) => s.node_ids.length > 0 || s.fact_count > 0);
      const totalNodes = sentences
        ? new Set(sentences.flatMap((s) => s.node_ids)).size
        : 0;
      const totalFacts = sentences
        ? sentences.reduce((sum, s) => sum + s.fact_count, 0)
        : 0;
      const isSelected = selectedParaKey === key;
      return { key, sentences, hasInfo, totalNodes, totalFacts, isSelected };
    },
    [paragraphMap, selectedParaKey]
  );

  const interactiveSection = (
    Tag: "p" | "h1" | "h2" | "h3" | "li",
    children: React.ReactNode,
    baseClass: string
  ) => {
    const { key, sentences, hasInfo, totalNodes, totalFacts, isSelected } =
      getSectionInfo(children);
    const countParts = [
      totalNodes > 0 ? `${totalNodes}n` : "",
      totalFacts > 0 ? `${totalFacts}f` : "",
    ]
      .filter(Boolean)
      .join(" ");

    const plainText = extractText(children);

    if (Tag === "li") {
      return (
        <li
          className={`${baseClass} transition-colors ${
            isSelected
              ? "bg-ocean-dim/50 dark:bg-ocean-dark/20"
              : hasInfo
                ? "hover:bg-secondary cursor-pointer"
                : ""
          }`}
          onClick={
            hasInfo && sentences
              ? (e: React.MouseEvent) => {
                  e.stopPropagation();
                  handleSectionClick(key, sentences, plainText);
                }
              : undefined
          }
        >
          {children}
          {hasInfo && countParts && (
            <span className={`text-[10px] font-mono ml-1.5 ${
              isSelected ? "text-ocean" : "text-muted-foreground"
            }`}>
              {countParts}
            </span>
          )}
        </li>
      );
    }

    return (
      <div className="flex items-stretch group">
        <Tag
          className={`${baseClass} flex-1 transition-colors ${
            isSelected
              ? "bg-ocean-dim/40 dark:bg-ocean-dark/20 border-l-2 border-ocean pl-3"
              : hasInfo
                ? "hover:bg-secondary cursor-pointer border-l-2 border-transparent hover:border-border pl-3"
                : "pl-3 border-l-2 border-transparent"
          }`}
          onClick={
            hasInfo && sentences
              ? () => handleSectionClick(key, sentences, plainText)
              : undefined
          }
        >
          {children}
        </Tag>
        <div className="w-10 shrink-0 flex flex-col items-center justify-center">
          {countParts && (
            <span
              className={`text-[10px] font-mono leading-tight ${
                isSelected
                  ? "text-ocean font-medium"
                  : "text-muted-foreground/60 group-hover:text-muted-foreground"
              }`}
            >
              {countParts}
            </span>
          )}
        </div>
      </div>
    );
  };

  const markdownComponents = useMemo(
    () => ({
      p: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection(
          "p",
          children,
          "mb-4 text-[0.95rem] leading-[1.8] text-foreground/85"
        ),
      h1: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection(
          "h1",
          children,
          "text-[1.5rem] font-semibold mt-10 mb-3 text-foreground"
        ),
      h2: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection(
          "h2",
          children,
          "text-[1.2rem] font-semibold mt-8 mb-2 text-foreground border-b border-border/50 pb-1"
        ),
      h3: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection(
          "h3",
          children,
          "text-[1.05rem] font-medium mt-6 mb-2 text-foreground/90"
        ),
      ol: ({ children }: { children?: React.ReactNode }) => (
        <ol className="list-decimal pl-6 mb-4 space-y-1">{children}</ol>
      ),
      ul: ({ children }: { children?: React.ReactNode }) => (
        <ul className="list-disc pl-6 mb-4 space-y-1">{children}</ul>
      ),
      li: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection(
          "li",
          children,
          "text-[0.95rem] leading-[1.8] text-foreground/85 rounded-sm py-0.5 px-1 -mx-1"
        ),
      a: ({
        href,
        children,
      }: {
        href?: string;
        children?: React.ReactNode;
      }) => {
        if (href?.startsWith("/nodes/") || href?.startsWith("/facts/")) {
          return (
            <Link
              href={href}
              className="text-ocean dark:text-ocean-mid underline decoration-dotted underline-offset-2 hover:decoration-solid hover:text-earth dark:hover:text-earth-mid"
              onClick={(e) => e.stopPropagation()}
            >
              {children}
            </Link>
          );
        }
        return (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-ocean dark:text-ocean-mid underline decoration-dotted underline-offset-2 hover:decoration-solid hover:text-earth dark:hover:text-earth-mid"
            onClick={(e) => e.stopPropagation()}
          >
            {children}
          </a>
        );
      },
      strong: ({ children }: { children?: React.ReactNode }) => (
        <strong className="font-semibold text-foreground">
          {children}
        </strong>
      ),
      em: ({ children }: { children?: React.ReactNode }) => (
        <em className="italic text-muted-foreground">
          {children}
        </em>
      ),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedParaKey, paragraphMap, getSectionInfo]
  );

  const hasDefinition = document.definition && document.definition.trim();

  return (
    <>
      {/* ── Document ── */}
      <article className="mx-auto max-w-4xl">
        {/* Header */}
        <header className="mb-8 pb-6 border-b border-border/50">
          <div className="flex items-center gap-2.5 mb-3">
            <Badge
              variant="outline"
              className="text-[0.6rem] uppercase tracking-[0.1em] font-semibold px-2.5 py-0.5 border-ocean/30 text-ocean dark:text-ocean-mid"
            >
              {document.node_type === "supersynthesis"
                ? "Super-Synthesis"
                : "Synthesis"}
            </Badge>
            <button
              type="button"
              onClick={toggleVisibility}
              disabled={togglingVisibility}
              className="flex items-center gap-1.5 text-[0.72rem] text-muted-foreground hover:text-foreground transition-colors"
              title={`Switch to ${visibility === "public" ? "private" : "public"}`}
            >
              <div
                className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors ${
                  visibility === "public"
                    ? "bg-ocean"
                    : "bg-border"
                }`}
              >
                <span
                  className={`inline-block size-3 rounded-full bg-white shadow-sm transition-transform ${
                    visibility === "public" ? "translate-x-3.5" : "translate-x-0.5"
                  }`}
                />
              </div>
              <span className="uppercase tracking-wider font-medium">
                {togglingVisibility ? "..." : visibility}
              </span>
            </button>
          </div>
          <h1 className="text-[2.1rem] font-semibold leading-tight text-foreground mb-2">
            {title}
          </h1>
          <div className="flex items-center gap-3 text-[0.82rem] text-muted-foreground">
            {(date || document.created_at) && (
              <span>
                {date ??
                  (document.created_at &&
                    new Date(document.created_at).toLocaleDateString(undefined, {
                      year: "numeric",
                      month: "long",
                      day: "numeric",
                    }))}
              </span>
            )}
            {document.sentences.length > 0 && (
              <>
                <span className="text-border">|</span>
                <span>{document.sentences.length} sections</span>
              </>
            )}
            {document.referenced_nodes.length > 0 && (
              <>
                <span className="text-border">|</span>
                <span>{document.referenced_nodes.length} nodes</span>
              </>
            )}
          </div>
        </header>

        {/* Body */}
        <div className="mb-10">
          {document.status === "error" ? (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-6">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 size-5 rounded-full bg-destructive/20 flex items-center justify-center flex-shrink-0">
                  <span className="text-destructive text-xs font-bold">!</span>
                </div>
                <div className="flex-1">
                  <p className="font-medium text-destructive mb-1">Synthesis Failed</p>
                  <p className="text-sm text-muted-foreground mb-4">
                    {document.error_message || "The synthesis agent was unable to produce a document."}
                  </p>
                  {onRegenerate && (
                    <button
                      type="button"
                      onClick={onRegenerate}
                      disabled={isRegenerating}
                      className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
                    >
                      {isRegenerating ? (
                        <>
                          <Loader2 className="size-4 animate-spin" />
                          Regenerating...
                        </>
                      ) : (
                        "Regenerate"
                      )}
                    </button>
                  )}
                </div>
              </div>
            </div>
          ) : hasDefinition ? (
            <Markdown components={markdownComponents}>
              {document.definition!}
            </Markdown>
          ) : (
            <p className="text-muted-foreground italic">No content available.</p>
          )}
        </div>

        {document.sub_syntheses.length > 0 && (
          <SubSynthesisList subSyntheses={document.sub_syntheses} />
        )}
        {document.referenced_nodes.length > 0 && (
          <SynthesisNodeList nodes={document.referenced_nodes} />
        )}
      </article>

      {/* ── Evidence Dialog ── */}
      <Dialog open={dialogOpen} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent className="sm:max-w-4xl w-full max-h-[85vh] overflow-hidden flex flex-col">
          <DialogHeader className="shrink-0">
            <DialogTitle className="text-[0.68rem] uppercase tracking-[0.1em] font-bold text-muted-foreground">
              Section Evidence
            </DialogTitle>
          </DialogHeader>

          {/* Selected paragraph */}
          <div className="shrink-0 border-l-2 border-ocean pl-4 py-2 bg-ocean-dim/20 dark:bg-ocean-dark/10 rounded-r">
            <p className="text-[0.85rem] leading-[1.65] text-muted-foreground italic max-h-24 overflow-y-auto">
              {selectedParaText.slice(0, 400)}
              {selectedParaText.length > 400 ? "..." : ""}
            </p>
          </div>

          <div className="overflow-y-auto flex-1 space-y-5 pr-1">
            {/* Nodes section */}
            {selectedNodes.length > 0 && (
              <div>
                <button
                  type="button"
                  className="flex items-center gap-2 w-full text-left mb-2"
                  onClick={() => setNodesOpen(!nodesOpen)}
                >
                  <ChevronRight
                    className={`size-3.5 text-muted-foreground transition-transform ${
                      nodesOpen ? "rotate-90" : ""
                    }`}
                  />
                  <span className="text-[0.68rem] uppercase tracking-[0.1em] font-bold text-muted-foreground">
                    Nodes
                  </span>
                  <span className="text-[0.68rem] text-muted-foreground/60 ml-auto">
                    {selectedNodes.length}
                  </span>
                </button>
                {nodesOpen && (
                  <div className="grid grid-cols-2 gap-2">
                    {selectedNodes.map((nid) => {
                      const nodeInfo = nodeMap.get(nid);
                      return (
                        <a
                          key={nid}
                          href={`/nodes/${nid}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center gap-2 rounded-md border px-3 py-2 text-[0.82rem] hover:bg-accent hover:border-ocean/30 transition-all group/node"
                        >
                          <CircleDot className="size-3 text-ocean shrink-0" />
                          <span className="truncate text-foreground/80 group-hover/node:text-ocean dark:group-hover/node:text-ocean-mid">
                            {nodeInfo?.concept ?? nid.slice(0, 8) + "..."}
                          </span>
                          {nodeInfo?.node_type && (
                            <span className="text-[0.6rem] uppercase tracking-wider text-muted-foreground/60 shrink-0 ml-auto">
                              {nodeInfo.node_type}
                            </span>
                          )}
                        </a>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* Facts section */}
            <div>
              <button
                type="button"
                className="flex items-center gap-2 w-full text-left mb-2"
                onClick={() => setFactsOpen(!factsOpen)}
              >
                <ChevronRight
                  className={`size-3.5 text-muted-foreground transition-transform ${
                    factsOpen ? "rotate-90" : ""
                  }`}
                />
                <span className="text-[0.68rem] uppercase tracking-[0.1em] font-bold text-muted-foreground">
                  Evidence
                </span>
                <span className="text-[0.68rem] text-muted-foreground/60 ml-auto">
                  {loadingFacts ? "..." : factLinks.length}
                </span>
              </button>
              {factsOpen && (
                <div>
                  {loadingFacts ? (
                    <div className="flex items-center gap-2 text-[0.82rem] text-muted-foreground py-6 justify-center">
                      <Loader2 className="size-4 animate-spin" />
                      Loading evidence...
                    </div>
                  ) : factLinks.length === 0 ? (
                    <p className="text-[0.82rem] text-muted-foreground italic py-4 text-center">
                      No evidence for this section.
                    </p>
                  ) : (
                    <div className="space-y-4">
                      {groupFactsBySource(factLinks).map((group) => (
                        <SourceFactGroup key={group.key} group={group} />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

// ── Source Fact Group ─────────────────────────────────────────────

function SourceFactGroup({ group }: { group: FactGroup }) {
  const [open, setOpen] = useState(true);

  return (
    <div>
      <button
        type="button"
        className="w-full text-left px-2 py-1.5 rounded hover:bg-accent transition-colors"
        onClick={() => setOpen(!open)}
      >
        <div className="flex items-center gap-1.5">
          <ChevronRight
            className={`size-3 text-muted-foreground shrink-0 transition-transform ${
              open ? "rotate-90" : ""
            }`}
          />
          <span
            className="text-[0.82rem] font-medium text-foreground/80 truncate flex-1"
            title={group.sourceUri || group.title}
          >
            {group.title || "Unknown source"}
          </span>
          {group.sourceUri && (
            <a
              href={group.sourceUri}
              target="_blank"
              rel="noopener noreferrer"
              className="shrink-0 text-muted-foreground hover:text-ocean transition-colors"
              onClick={(e) => e.stopPropagation()}
              title={group.sourceUri}
            >
              <ExternalLink className="size-3" />
            </a>
          )}
          <span className="text-[0.68rem] font-mono text-muted-foreground/60 shrink-0">
            {group.facts.length}
          </span>
        </div>
        {group.author && (
          <p className="text-[0.75rem] text-muted-foreground pl-[18px] mt-0.5 italic">
            {group.author}
          </p>
        )}
      </button>

      {open &&
        group.facts.map((fl) => (
          <a
            key={fl.fact_id}
            href={`/facts/${fl.fact_id}`}
            target="_blank"
            rel="noopener noreferrer"
            className="block border-l-2 border-border hover:border-ocean ml-[18px] pl-3 py-2 transition-colors group/fact"
          >
            <p className="text-[0.82rem] leading-[1.65] text-foreground/70 line-clamp-3 group-hover/fact:text-foreground">
              {fl.content || fl.fact_id.slice(0, 12) + "..."}
            </p>
            <div className="flex items-center gap-2 mt-1">
              {fl.fact_type && (
                <span className="text-[0.65rem] uppercase tracking-wider font-medium text-earth dark:text-earth-mid">
                  {fl.fact_type}
                </span>
              )}
              <span className="text-[0.65rem] font-mono text-muted-foreground/60 ml-auto">
                {(fl.embedding_distance * 100).toFixed(0)}%
              </span>
            </div>
          </a>
        ))}
    </div>
  );
}
