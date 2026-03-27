"use client";

import { useState, useMemo, useCallback } from "react";
import Link from "next/link";
import Markdown from "react-markdown";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  ChevronRight,
  CircleDot,
  ExternalLink,
  FileText,
  Loader2,
} from "lucide-react";
import { formatSynthesisConcept } from "./utils";
import type {
  SynthesisDocumentResponse,
  SynthesisSentenceResponse,
  SynthesisNodeResponse,
  SentenceFactLink,
} from "@/types";
import { getSentenceFacts } from "@/lib/api";
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
      if (
        plainSentence.length > 10 &&
        plainPara.includes(plainSentence.slice(0, 40))
      ) {
        matched.push(s);
      }
    }

    if (matched.length > 0) {
      map.set(para.slice(0, 60).trim(), matched);
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
  const [selectedNodes, setSelectedNodes] = useState<string[]>([]);
  const [selectedSentences, setSelectedSentences] = useState<
    SynthesisSentenceResponse[]
  >([]);
  const [factLinks, setFactLinks] = useState<SentenceFactLink[]>([]);
  const [loadingFacts, setLoadingFacts] = useState(false);
  const [nodesVisible, setNodesVisible] = useState(true);
  const [factsVisible, setFactsVisible] = useState(true);

  const hasSidePanel = selectedParaKey !== null;
  const { title, date } = formatSynthesisConcept(document.concept);

  const nodeMap = useMemo(() => {
    const map = new Map<string, SynthesisNodeResponse>();
    for (const n of document.referenced_nodes) {
      map.set(n.node_id, n);
    }
    return map;
  }, [document.referenced_nodes]);

  const paragraphMap = useMemo(
    () => buildParagraphSentenceMap(document.definition || "", document.sentences),
    [document.definition, document.sentences]
  );

  const handleSectionClick = useCallback(
    async (key: string, sentences: SynthesisSentenceResponse[]) => {
      if (selectedParaKey === key) {
        setSelectedParaKey(null);
        setSelectedNodes([]);
        setSelectedSentences([]);
        setFactLinks([]);
        return;
      }
      setSelectedParaKey(key);
      setSelectedSentences(sentences);

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
    [selectedParaKey, document.id]
  );

  const getSectionInfo = useCallback(
    (children: React.ReactNode) => {
      const text = extractText(children);
      const key = text.slice(0, 60).trim();
      const sentences = paragraphMap.get(key);
      const hasInfo = sentences && sentences.some((s) => s.node_ids.length > 0 || s.fact_count > 0);
      const totalNodes = sentences ? new Set(sentences.flatMap((s) => s.node_ids)).size : 0;
      const totalFacts = sentences ? sentences.reduce((sum, s) => sum + s.fact_count, 0) : 0;
      const isSelected = selectedParaKey === key;
      return { key, sentences, hasInfo, totalNodes, totalFacts, isSelected };
    },
    [paragraphMap, selectedParaKey]
  );

  // Interactive section wrapper
  const interactiveSection = (
    Tag: "p" | "h1" | "h2" | "h3" | "li",
    children: React.ReactNode,
    baseClass: string
  ) => {
    const { key, sentences, hasInfo, totalNodes, totalFacts, isSelected } = getSectionInfo(children);
    const countParts = [
      totalNodes > 0 ? `${totalNodes}n` : "",
      totalFacts > 0 ? `${totalFacts}f` : "",
    ].filter(Boolean).join(" ");

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
              ? Tag === "li"
                ? (e: React.MouseEvent) => { e.stopPropagation(); handleSectionClick(key, sentences); }
                : () => handleSectionClick(key, sentences)
              : undefined
          }
        >
          {children}
        </Tag>
        <div className="w-10 shrink-0 flex flex-col items-center justify-center">
          {countParts && (
            <span className={`text-[10px] font-mono leading-tight ${
              isSelected ? "text-blue-500 font-medium" : "text-stone-400 group-hover:text-stone-500"
            }`}>
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
        interactiveSection("p", children, "mb-4 text-[0.95rem] leading-[1.75] text-stone-800 dark:text-stone-200"),
      h1: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection("h1", children, "text-[1.5rem] font-semibold mt-8 mb-3 text-stone-900 dark:text-stone-100"),
      h2: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection("h2", children, "text-[1.2rem] font-semibold mt-6 mb-2 text-stone-900 dark:text-stone-100"),
      h3: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection("h3", children, "text-[1.05rem] font-medium mt-5 mb-2 text-stone-800 dark:text-stone-200"),
      li: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection("li", children, "mb-1.5 text-[0.95rem] leading-[1.75] text-stone-800 dark:text-stone-200"),
      a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
        if (href?.startsWith("/nodes/") || href?.startsWith("/facts/")) {
          return (
            <Link href={href} className="text-blue-700 dark:text-blue-400 underline decoration-dotted underline-offset-2 hover:decoration-solid" onClick={(e) => e.stopPropagation()}>
              {children}
            </Link>
          );
        }
        return (
          <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-700 dark:text-blue-400 underline decoration-dotted underline-offset-2 hover:decoration-solid" onClick={(e) => e.stopPropagation()}>
            {children}
          </a>
        );
      },
      strong: ({ children }: { children?: React.ReactNode }) => (
        <strong className="font-semibold text-stone-900 dark:text-stone-100">{children}</strong>
      ),
      em: ({ children }: { children?: React.ReactNode }) => (
        <em className="italic text-stone-600 dark:text-stone-400">{children}</em>
      ),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedParaKey, paragraphMap, getSectionInfo]
  );

  const hasDefinition = document.definition && document.definition.trim();

  return (
    <div className={`flex transition-all duration-200 ${hasSidePanel ? "gap-3" : "gap-0"}`}>
      {/* ── Main Document ── */}
      <div className={`min-w-0 transition-all duration-200 ${hasSidePanel ? "basis-2/3" : "flex-1 mx-auto max-w-4xl"}`}>
        {/* Header */}
        <div className="mb-6">
          <div className="flex items-center gap-2 mb-1">
            <Badge
              variant="outline"
              className="text-[0.65rem] uppercase tracking-wider font-semibold px-2 py-0.5"
            >
              {document.node_type === "supersynthesis" ? "Super-Synthesis" : "Synthesis"}
            </Badge>
            <Badge
              variant={document.visibility === "public" ? "default" : "secondary"}
              className="text-[0.65rem] uppercase tracking-wider px-2 py-0.5"
            >
              {document.visibility}
            </Badge>
          </div>
          <h1 className="text-[2rem] font-semibold leading-tight text-stone-900 dark:text-stone-100 mb-1">
            {title}
          </h1>
          {(date || document.created_at) && (
            <p className="text-[0.82rem] text-stone-500 dark:text-stone-400">
              {date ?? (document.created_at && new Date(document.created_at).toLocaleDateString(undefined, {
                year: "numeric", month: "long", day: "numeric",
              }))}
            </p>
          )}
        </div>

        {/* Document body */}
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

      {/* ── Evidence Sidebar ── */}
      {hasSidePanel && (
        <div className="basis-1/3 shrink-0 min-w-0">
          <div className="sticky top-4 space-y-4">
            {/* Nodes Section */}
            <div className="rounded-lg border border-stone-200 dark:border-stone-800 bg-white dark:bg-stone-950 overflow-hidden">
              <button
                type="button"
                className="flex items-center gap-2 w-full px-3 py-2 text-left hover:bg-stone-50 dark:hover:bg-stone-900 transition-colors"
                onClick={() => setNodesVisible(!nodesVisible)}
              >
                <ChevronRight className={`size-3.5 text-stone-400 transition-transform ${nodesVisible ? "rotate-90" : ""}`} />
                <span className="text-[0.68rem] uppercase tracking-[0.1em] font-bold text-stone-500 dark:text-stone-400">
                  Nodes
                </span>
                <span className="text-[0.68rem] text-stone-400 ml-auto">{selectedNodes.length}</span>
              </button>
              {nodesVisible && selectedNodes.length > 0 && (
                <div className="px-2 pb-2 space-y-0.5 max-h-[30vh] overflow-y-auto">
                  {selectedNodes.map((nid) => {
                    const nodeInfo = nodeMap.get(nid);
                    return (
                      <a
                        key={nid}
                        href={`/nodes/${nid}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-2 rounded px-2 py-1.5 text-[0.82rem] hover:bg-stone-50 dark:hover:bg-stone-900 transition-colors group/node"
                      >
                        <CircleDot className="size-3 text-blue-500 shrink-0" />
                        <span className="truncate text-stone-700 dark:text-stone-300 group-hover/node:text-blue-700 dark:group-hover/node:text-blue-400">
                          {nodeInfo?.concept ?? nid.slice(0, 8) + "..."}
                        </span>
                        {nodeInfo?.node_type && (
                          <span className="text-[0.65rem] uppercase tracking-wider text-stone-400 shrink-0 ml-auto">
                            {nodeInfo.node_type}
                          </span>
                        )}
                      </a>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Facts Section */}
            <div className="rounded-lg border border-stone-200 dark:border-stone-800 bg-white dark:bg-stone-950 overflow-hidden">
              <button
                type="button"
                className="flex items-center gap-2 w-full px-3 py-2 text-left hover:bg-stone-50 dark:hover:bg-stone-900 transition-colors"
                onClick={() => setFactsVisible(!factsVisible)}
              >
                <ChevronRight className={`size-3.5 text-stone-400 transition-transform ${factsVisible ? "rotate-90" : ""}`} />
                <span className="text-[0.68rem] uppercase tracking-[0.1em] font-bold text-stone-500 dark:text-stone-400">
                  Evidence
                </span>
                <span className="text-[0.68rem] text-stone-400 ml-auto">
                  {loadingFacts ? "..." : factLinks.length}
                </span>
              </button>
              {factsVisible && (
                <div className="px-2 pb-2 max-h-[50vh] overflow-y-auto">
                  {loadingFacts ? (
                    <div className="flex items-center gap-2 text-[0.82rem] text-stone-400 py-4 justify-center">
                      <Loader2 className="size-3.5 animate-spin" />
                      Loading evidence...
                    </div>
                  ) : factLinks.length === 0 ? (
                    <p className="text-[0.82rem] text-stone-400 italic py-3 text-center">
                      No evidence for this section.
                    </p>
                  ) : (
                    <div className="space-y-3 pt-1">
                      {groupFactsBySource(factLinks).map((group) => (
                        <SourceFactGroup key={group.key} group={group} />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Source Fact Group ─────────────────────────────────────────────

function SourceFactGroup({ group }: { group: FactGroup }) {
  const [open, setOpen] = useState(true);

  return (
    <div>
      {/* Source header */}
      <button
        type="button"
        className="w-full text-left px-2 py-1.5 rounded hover:bg-stone-50 dark:hover:bg-stone-900 transition-colors"
        onClick={() => setOpen(!open)}
      >
        <div className="flex items-center gap-1.5">
          <ChevronRight className={`size-3 text-stone-400 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} />
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

      {/* Facts */}
      {open && (
        <div className="space-y-1 mt-1 ml-[18px]">
          {group.facts.map((fl) => (
            <a
              key={fl.fact_id}
              href={`/facts/${fl.fact_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="block border-l-2 border-stone-200 dark:border-stone-700 hover:border-blue-400 pl-3 py-1.5 transition-colors group/fact"
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
      )}
    </div>
  );
}
