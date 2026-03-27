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
  FileText,
  Loader2,
  X,
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

  const paragraphs = definition
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);

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
      // Use stripped plain text as key — must match what extractText()
      // returns from react-markdown rendered children
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
    return extractText((children as React.ReactElement).props?.children);
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

export function SynthesisDocument({ document }: SynthesisDocumentProps) {
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
    Tag: "p" | "h1" | "h2" | "h3",
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

    return (
      <div className="flex items-stretch group">
        <Tag
          className={`${baseClass} flex-1 transition-colors ${
            isSelected
              ? "bg-blue-50 dark:bg-blue-950/30 border-l-2 border-blue-400 pl-3"
              : hasInfo
                ? "hover:bg-stone-50 dark:hover:bg-stone-900/30 cursor-pointer border-l-2 border-transparent hover:border-stone-300 pl-3"
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
                  ? "text-blue-500 font-medium"
                  : "text-stone-400 group-hover:text-stone-500"
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
          "mb-4 text-[0.95rem] leading-[1.75] text-stone-800 dark:text-stone-200"
        ),
      h1: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection(
          "h1",
          children,
          "text-[1.5rem] font-semibold mt-8 mb-3 text-stone-900 dark:text-stone-100"
        ),
      h2: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection(
          "h2",
          children,
          "text-[1.2rem] font-semibold mt-6 mb-2 text-stone-900 dark:text-stone-100"
        ),
      h3: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection(
          "h3",
          children,
          "text-[1.05rem] font-medium mt-5 mb-2 text-stone-800 dark:text-stone-200"
        ),
      ol: ({ children }: { children?: React.ReactNode }) => {
        const { key, sentences, hasInfo, isSelected } = getSectionInfo(children);
        const plainText = extractText(children);
        return (
          <ol
            className={`list-decimal pl-6 mb-4 space-y-1 rounded transition-colors ${
              isSelected
                ? "bg-blue-50 dark:bg-blue-950/30"
                : hasInfo
                  ? "hover:bg-stone-50 dark:hover:bg-stone-900/30 cursor-pointer"
                  : ""
            }`}
            onClick={
              hasInfo && sentences
                ? () => handleSectionClick(key, sentences, plainText)
                : undefined
            }
          >
            {children}
          </ol>
        );
      },
      ul: ({ children }: { children?: React.ReactNode }) => {
        const { key, sentences, hasInfo, isSelected } = getSectionInfo(children);
        const plainText = extractText(children);
        return (
          <ul
            className={`list-disc pl-6 mb-4 space-y-1 rounded transition-colors ${
              isSelected
                ? "bg-blue-50 dark:bg-blue-950/30"
                : hasInfo
                  ? "hover:bg-stone-50 dark:hover:bg-stone-900/30 cursor-pointer"
                  : ""
            }`}
            onClick={
              hasInfo && sentences
                ? () => handleSectionClick(key, sentences, plainText)
                : undefined
            }
          >
            {children}
          </ul>
        );
      },
      li: ({ children }: { children?: React.ReactNode }) => (
        <li className="text-[0.95rem] leading-[1.75] text-stone-800 dark:text-stone-200">
          {children}
        </li>
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
              className="text-blue-700 dark:text-blue-400 underline decoration-dotted underline-offset-2 hover:decoration-solid"
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
            className="text-blue-700 dark:text-blue-400 underline decoration-dotted underline-offset-2 hover:decoration-solid"
            onClick={(e) => e.stopPropagation()}
          >
            {children}
          </a>
        );
      },
      strong: ({ children }: { children?: React.ReactNode }) => (
        <strong className="font-semibold text-stone-900 dark:text-stone-100">
          {children}
        </strong>
      ),
      em: ({ children }: { children?: React.ReactNode }) => (
        <em className="italic text-stone-600 dark:text-stone-400">
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
      <div className="mx-auto max-w-4xl">
        {/* Header */}
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-1">
            <Badge
              variant="outline"
              className="text-[0.65rem] uppercase tracking-wider font-semibold px-2 py-0.5"
            >
              {document.node_type === "supersynthesis"
                ? "Super-Synthesis"
                : "Synthesis"}
            </Badge>
            <button
              type="button"
              onClick={toggleVisibility}
              disabled={togglingVisibility}
              className="flex items-center gap-1.5 text-[0.72rem] text-stone-500 hover:text-stone-700 dark:hover:text-stone-300 transition-colors"
              title={`Switch to ${visibility === "public" ? "private" : "public"}`}
            >
              <div
                className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors ${
                  visibility === "public"
                    ? "bg-blue-500"
                    : "bg-stone-300 dark:bg-stone-600"
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
          <h1 className="text-[2rem] font-semibold leading-tight text-stone-900 dark:text-stone-100 mb-1">
            {title}
          </h1>
          {(date || document.created_at) && (
            <p className="text-[0.82rem] text-stone-500 dark:text-stone-400">
              {date ??
                (document.created_at &&
                  new Date(document.created_at).toLocaleDateString(undefined, {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  }))}
            </p>
          )}
        </div>

        {/* Body */}
        <div className="mb-8">
          {hasDefinition ? (
            <Markdown components={markdownComponents}>
              {document.definition!}
            </Markdown>
          ) : (
            <p className="text-stone-500 italic">No content available.</p>
          )}
        </div>

        {document.sub_syntheses.length > 0 && (
          <SubSynthesisList subSyntheses={document.sub_syntheses} />
        )}
        {document.referenced_nodes.length > 0 && (
          <SynthesisNodeList nodes={document.referenced_nodes} />
        )}
      </div>

      {/* ── Evidence Dialog ── */}
      <Dialog open={dialogOpen} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent className="sm:max-w-4xl w-full max-h-[85vh] overflow-hidden flex flex-col">
          <DialogHeader className="shrink-0">
            <DialogTitle className="text-[0.68rem] uppercase tracking-[0.1em] font-bold text-stone-400">
              Section Evidence
            </DialogTitle>
          </DialogHeader>

          <div className="overflow-y-auto flex-1 space-y-5 pr-1">
            {/* Selected paragraph */}
            <div className="border-l-2 border-blue-400 pl-4 py-1">
              <p className="text-[0.9rem] leading-[1.7] text-stone-700 dark:text-stone-300 italic">
                {selectedParaText.slice(0, 300)}
                {selectedParaText.length > 300 ? "..." : ""}
              </p>
            </div>

            {/* Nodes section */}
            {selectedNodes.length > 0 && (
              <div>
                <button
                  type="button"
                  className="flex items-center gap-2 w-full text-left mb-2"
                  onClick={() => setNodesOpen(!nodesOpen)}
                >
                  <ChevronRight
                    className={`size-3.5 text-stone-400 transition-transform ${
                      nodesOpen ? "rotate-90" : ""
                    }`}
                  />
                  <span className="text-[0.68rem] uppercase tracking-[0.1em] font-bold text-stone-500 dark:text-stone-400">
                    Nodes
                  </span>
                  <span className="text-[0.68rem] text-stone-400 ml-auto">
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
                          className="flex items-center gap-2 rounded-md border border-stone-200 dark:border-stone-800 px-3 py-2 text-[0.82rem] hover:bg-stone-50 dark:hover:bg-stone-900 hover:border-stone-300 transition-all group/node"
                        >
                          <CircleDot className="size-3 text-blue-500 shrink-0" />
                          <span className="truncate text-stone-700 dark:text-stone-300 group-hover/node:text-blue-700 dark:group-hover/node:text-blue-400">
                            {nodeInfo?.concept ?? nid.slice(0, 8) + "..."}
                          </span>
                          {nodeInfo?.node_type && (
                            <span className="text-[0.6rem] uppercase tracking-wider text-stone-400 shrink-0 ml-auto">
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
                  className={`size-3.5 text-stone-400 transition-transform ${
                    factsOpen ? "rotate-90" : ""
                  }`}
                />
                <span className="text-[0.68rem] uppercase tracking-[0.1em] font-bold text-stone-500 dark:text-stone-400">
                  Evidence
                </span>
                <span className="text-[0.68rem] text-stone-400 ml-auto">
                  {loadingFacts ? "..." : factLinks.length}
                </span>
              </button>
              {factsOpen && (
                <div>
                  {loadingFacts ? (
                    <div className="flex items-center gap-2 text-[0.82rem] text-stone-400 py-6 justify-center">
                      <Loader2 className="size-4 animate-spin" />
                      Loading evidence...
                    </div>
                  ) : factLinks.length === 0 ? (
                    <p className="text-[0.82rem] text-stone-400 italic py-4 text-center">
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
        className="w-full text-left px-2 py-1.5 rounded hover:bg-stone-50 dark:hover:bg-stone-900 transition-colors"
        onClick={() => setOpen(!open)}
      >
        <div className="flex items-center gap-1.5">
          <ChevronRight
            className={`size-3 text-stone-400 shrink-0 transition-transform ${
              open ? "rotate-90" : ""
            }`}
          />
          <span
            className="text-[0.82rem] font-medium text-stone-700 dark:text-stone-300 truncate flex-1"
            title={group.sourceUri || group.title}
          >
            {group.title || "Unknown source"}
          </span>
          {group.sourceUri && (
            <a
              href={group.sourceUri}
              target="_blank"
              rel="noopener noreferrer"
              className="shrink-0 text-stone-400 hover:text-blue-500 transition-colors"
              onClick={(e) => e.stopPropagation()}
              title={group.sourceUri}
            >
              <ExternalLink className="size-3" />
            </a>
          )}
          <span className="text-[0.68rem] font-mono text-stone-400 shrink-0">
            {group.facts.length}
          </span>
        </div>
        {group.author && (
          <p className="text-[0.75rem] text-stone-500 dark:text-stone-400 pl-[18px] mt-0.5 italic">
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
            className="block border-l-2 border-stone-200 dark:border-stone-700 hover:border-blue-400 ml-[18px] pl-3 py-2 transition-colors group/fact"
          >
            <p className="text-[0.82rem] leading-[1.6] text-stone-700 dark:text-stone-300 line-clamp-3 group-hover/fact:text-stone-900 dark:group-hover/fact:text-stone-100">
              {fl.content || fl.fact_id.slice(0, 12) + "..."}
            </p>
            <div className="flex items-center gap-2 mt-1">
              {fl.fact_type && (
                <span className="text-[0.65rem] uppercase tracking-wider font-medium text-stone-400">
                  {fl.fact_type}
                </span>
              )}
              <span className="text-[0.65rem] font-mono text-stone-400 ml-auto">
                {(fl.embedding_distance * 100).toFixed(0)}%
              </span>
            </div>
          </a>
        ))}
    </div>
  );
}
