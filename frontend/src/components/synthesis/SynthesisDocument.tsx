"use client";

import { useState, useMemo, useCallback } from "react";
import Link from "next/link";
import Markdown from "react-markdown";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  CircleDot,
  FileText,
  Loader2,
  PanelLeftClose,
  PanelRightClose,
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

interface SynthesisDocumentProps {
  document: SynthesisDocumentResponse;
}

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
      const key = para.slice(0, 60).trim();
      map.set(key, matched);
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
    return extractText(
      (children as React.ReactElement).props?.children
    );
  }
  return "";
}

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

      // Collect unique node IDs
      const nodeIds = new Set<string>();
      for (const s of sentences) {
        for (const nid of s.node_ids) {
          nodeIds.add(nid);
        }
      }
      setSelectedNodes(Array.from(nodeIds));

      // Lazy-load facts for all sentences in this section
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
      const hasInfo =
        sentences &&
        sentences.some(
          (s) => s.node_ids.length > 0 || s.fact_count > 0
        );
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

  // Shared interactive section wrapper
  const interactiveSection = (
    Tag: "p" | "h1" | "h2" | "h3" | "li",
    children: React.ReactNode,
    baseClass: string
  ) => {
    const { key, sentences, hasInfo, totalNodes, totalFacts, isSelected } =
      getSectionInfo(children);

    const badgeText = [
      totalNodes > 0 ? `${totalNodes}n` : "",
      totalFacts > 0 ? `${totalFacts}f` : "",
    ]
      .filter(Boolean)
      .join(" ");

    return (
      <Tag
        className={`${baseClass} rounded-sm transition-colors flex items-start gap-2 ${
          isSelected
            ? "bg-primary/5 ring-1 ring-primary/20 px-2 -mx-2"
            : hasInfo
              ? "hover:bg-muted/30 cursor-pointer px-2 -mx-2"
              : ""
        }`}
        onClick={
          hasInfo && sentences
            ? Tag === "li"
              ? (e: React.MouseEvent) => {
                  e.stopPropagation();
                  handleSectionClick(key, sentences);
                }
              : () => handleSectionClick(key, sentences)
            : undefined
        }
      >
        <span className="flex-1">{children}</span>
        {hasInfo && badgeText && (
          <Badge
            variant={isSelected ? "default" : "outline"}
            className="text-[10px] px-1 py-0 shrink-0 mt-1"
          >
            {badgeText}
          </Badge>
        )}
      </Tag>
    );
  };

  const markdownComponents = useMemo(
    () => ({
      p: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection("p", children, "mb-4 leading-relaxed"),
      h1: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection("h1", children, "text-2xl font-bold mt-6 mb-3"),
      h2: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection("h2", children, "text-xl font-semibold mt-5 mb-2"),
      h3: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection("h3", children, "text-lg font-medium mt-4 mb-2"),
      li: ({ children }: { children?: React.ReactNode }) =>
        interactiveSection("li", children, "mb-1"),
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
              className="text-primary underline underline-offset-2 hover:text-primary/80"
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
            className="text-primary underline underline-offset-2"
            onClick={(e) => e.stopPropagation()}
          >
            {children}
          </a>
        );
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedParaKey, paragraphMap, getSectionInfo]
  );

  const hasDefinition = document.definition && document.definition.trim();

  return (
    <div className="flex gap-4">
      {/* Main document */}
      <div className="flex-1 min-w-0">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <CardTitle className="text-2xl">
                {formatSynthesisConcept(document.concept).title}
              </CardTitle>
              <Badge variant="outline">
                {document.node_type === "supersynthesis"
                  ? "Super-Synthesis"
                  : "Synthesis"}
              </Badge>
              <Badge
                variant={
                  document.visibility === "public" ? "default" : "secondary"
                }
              >
                {document.visibility}
              </Badge>
            </div>
            {(formatSynthesisConcept(document.concept).date ||
              document.created_at) && (
              <p className="text-sm text-muted-foreground">
                {formatSynthesisConcept(document.concept).date ??
                  (document.created_at &&
                    new Date(document.created_at).toLocaleDateString())}
              </p>
            )}
          </CardHeader>
          <CardContent className="prose prose-sm dark:prose-invert max-w-none">
            {hasDefinition ? (
              <Markdown components={markdownComponents}>
                {document.definition!}
              </Markdown>
            ) : (
              <p className="text-muted-foreground">No content available.</p>
            )}
          </CardContent>
        </Card>

        {document.sub_syntheses.length > 0 && (
          <SubSynthesisList subSyntheses={document.sub_syntheses} />
        )}
        {document.referenced_nodes.length > 0 && (
          <SynthesisNodeList nodes={document.referenced_nodes} />
        )}
      </div>

      {/* Nodes panel */}
      {hasSidePanel && (
        <div
          className={`shrink-0 transition-all duration-200 ${
            nodesVisible ? "w-72" : "w-8"
          }`}
        >
          {nodesVisible ? (
            <Card className="sticky top-4">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-xs font-medium flex items-center gap-1.5">
                  <CircleDot className="size-3" />
                  Nodes ({selectedNodes.length})
                </CardTitle>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-5"
                  onClick={() => setNodesVisible(false)}
                  title="Minimize"
                >
                  <PanelRightClose className="size-3.5" />
                </Button>
              </CardHeader>
              <CardContent className="space-y-1 max-h-[70vh] overflow-y-auto pt-0">
                {selectedNodes.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    No nodes in this section.
                  </p>
                ) : (
                  selectedNodes.map((nid) => {
                    const nodeInfo = nodeMap.get(nid);
                    return (
                      <Link
                        key={nid}
                        href={`/nodes/${nid}`}
                        className="flex items-center gap-1.5 rounded border px-2 py-1.5 text-xs hover:bg-accent transition-colors"
                      >
                        <CircleDot className="size-3 text-primary shrink-0" />
                        <span className="truncate">
                          {nodeInfo?.concept ?? nid.slice(0, 8) + "..."}
                        </span>
                        {nodeInfo?.node_type && (
                          <Badge
                            variant="outline"
                            className="text-[9px] px-1 py-0 shrink-0 ml-auto"
                          >
                            {nodeInfo.node_type}
                          </Badge>
                        )}
                      </Link>
                    );
                  })
                )}
              </CardContent>
            </Card>
          ) : (
            <Button
              variant="outline"
              size="icon"
              className="sticky top-4 size-8"
              onClick={() => setNodesVisible(true)}
              title="Show nodes"
            >
              <CircleDot className="size-3.5" />
            </Button>
          )}
        </div>
      )}

      {/* Facts panel */}
      {hasSidePanel && (
        <div
          className={`shrink-0 transition-all duration-200 ${
            factsVisible ? "w-96" : "w-8"
          }`}
        >
          {factsVisible ? (
            <Card className="sticky top-4">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-xs font-medium flex items-center gap-1.5">
                  <FileText className="size-3" />
                  Facts ({loadingFacts ? "..." : factLinks.length})
                </CardTitle>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-5"
                  onClick={() => setFactsVisible(false)}
                  title="Minimize"
                >
                  <PanelRightClose className="size-3.5" />
                </Button>
              </CardHeader>
              <CardContent className="space-y-1 max-h-[70vh] overflow-y-auto pt-0">
                {loadingFacts ? (
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground py-2">
                    <Loader2 className="size-3 animate-spin" />
                    Loading facts...
                  </div>
                ) : factLinks.length === 0 ? (
                  <p className="text-xs text-muted-foreground">
                    No facts in this section.
                  </p>
                ) : (
                  factLinks.map((fl) => (
                    <Link
                      key={fl.fact_id}
                      href={`/facts/${fl.fact_id}`}
                      className="block rounded border px-2.5 py-2 text-xs hover:bg-accent transition-colors space-y-1"
                    >
                      <p className="leading-relaxed line-clamp-3">
                        {fl.content || fl.fact_id.slice(0, 12) + "..."}
                      </p>
                      <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                        {fl.fact_type && (
                          <Badge
                            variant="secondary"
                            className="text-[9px] px-1 py-0"
                          >
                            {fl.fact_type}
                          </Badge>
                        )}
                        {fl.source_title && (
                          <span className="truncate">{fl.source_title}</span>
                        )}
                        <span className="shrink-0 ml-auto">
                          {(fl.embedding_distance * 100).toFixed(0)}%
                        </span>
                      </div>
                    </Link>
                  ))
                )}
              </CardContent>
            </Card>
          ) : (
            <Button
              variant="outline"
              size="icon"
              className="sticky top-4 size-8"
              onClick={() => setFactsVisible(true)}
              title="Show facts"
            >
              <FileText className="size-3.5" />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
