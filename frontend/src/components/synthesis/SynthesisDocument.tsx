"use client";

import { useState, useMemo, useCallback } from "react";
import Link from "next/link";
import Markdown from "react-markdown";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { X, CircleDot, FileText, Loader2 } from "lucide-react";
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

/**
 * Build a map from paragraph text (first 60 chars) to the sentences it contains.
 * A paragraph may contain multiple sentences.
 */
function buildParagraphSentenceMap(
  definition: string,
  sentences: SynthesisSentenceResponse[]
): Map<string, SynthesisSentenceResponse[]> {
  const map = new Map<string, SynthesisSentenceResponse[]>();
  if (!definition || sentences.length === 0) return map;

  // Split definition into paragraphs (by double newline or heading)
  const paragraphs = definition.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);

  for (const para of paragraphs) {
    // Strip markdown formatting for matching
    const plainPara = para
      .replace(/^#{1,6}\s+/, "")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/\*([^*]+)\*/g, "$1");

    const matched: SynthesisSentenceResponse[] = [];
    for (const s of sentences) {
      // Strip links/formatting from sentence text too
      const plainSentence = s.text
        .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
        .replace(/\*\*([^*]+)\*\*/g, "$1")
        .replace(/\*([^*]+)\*/g, "$1")
        .trim();
      // Check if this sentence's text appears in the paragraph
      if (plainSentence.length > 10 && plainPara.includes(plainSentence.slice(0, 40))) {
        matched.push(s);
      }
    }

    if (matched.length > 0) {
      // Use first 60 chars of the raw paragraph as key
      const key = para.slice(0, 60).trim();
      map.set(key, matched);
    }
  }

  return map;
}

export function SynthesisDocument({ document }: SynthesisDocumentProps) {
  const [selectedSentences, setSelectedSentences] = useState<SynthesisSentenceResponse[] | null>(null);
  const [selectedParaKey, setSelectedParaKey] = useState<string | null>(null);
  const [factLinks, setFactLinks] = useState<SentenceFactLink[] | null>(null);
  const [loadingFacts, setLoadingFacts] = useState(false);
  const [activeSentencePos, setActiveSentencePos] = useState<number | null>(null);

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

  const handleParaClick = useCallback(
    (paraKey: string, sentences: SynthesisSentenceResponse[]) => {
      if (selectedParaKey === paraKey) {
        setSelectedSentences(null);
        setSelectedParaKey(null);
        setFactLinks(null);
        setActiveSentencePos(null);
        return;
      }
      setSelectedSentences(sentences);
      setSelectedParaKey(paraKey);
      setFactLinks(null);
      setActiveSentencePos(null);
    },
    [selectedParaKey]
  );

  const handleSentenceFactLoad = useCallback(
    async (sentence: SynthesisSentenceResponse) => {
      if (activeSentencePos === sentence.position) {
        setActiveSentencePos(null);
        setFactLinks(null);
        return;
      }
      setActiveSentencePos(sentence.position);
      if (sentence.fact_count > 0) {
        setLoadingFacts(true);
        try {
          const facts = await getSentenceFacts(document.id, sentence.position);
          setFactLinks(facts);
        } catch {
          setFactLinks([]);
        } finally {
          setLoadingFacts(false);
        }
      } else {
        setFactLinks([]);
      }
    },
    [activeSentencePos, document.id]
  );

  // Custom markdown components
  const markdownComponents = useMemo(
    () => ({
      p: ({ children }: { children?: React.ReactNode }) => {
        // Extract plain text to find matching sentences
        const text = extractText(children);
        const key = text.slice(0, 60).trim();
        const sentences = paragraphMap.get(key);
        const isSelected = selectedParaKey === key;
        const hasInfo = sentences && sentences.some((s) => s.fact_count > 0 || s.node_ids.length > 0);
        const totalFacts = sentences?.reduce((sum, s) => sum + s.fact_count, 0) ?? 0;
        const totalNodes = sentences?.reduce((sum, s) => sum + s.node_ids.length, 0) ?? 0;

        return (
          <p
            className={`mb-4 leading-relaxed rounded-sm transition-colors ${
              isSelected
                ? "bg-primary/5 ring-1 ring-primary/20 px-2 py-1 -mx-2"
                : hasInfo
                  ? "hover:bg-muted/30 cursor-pointer px-2 py-1 -mx-2"
                  : ""
            }`}
            onClick={
              hasInfo && sentences
                ? () => handleParaClick(key, sentences)
                : undefined
            }
          >
            {children}
            {hasInfo && (
              <>
                {" "}
                <Badge
                  variant={isSelected ? "default" : "outline"}
                  className="text-[10px] px-1 py-0 align-super"
                >
                  {totalNodes > 0 && `${totalNodes}n`}
                  {totalNodes > 0 && totalFacts > 0 && " "}
                  {totalFacts > 0 && `${totalFacts}f`}
                </Badge>
              </>
            )}
          </p>
        );
      },
      a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
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
      h1: ({ children }: { children?: React.ReactNode }) => (
        <h1 className="text-2xl font-bold mt-6 mb-3">{children}</h1>
      ),
      h2: ({ children }: { children?: React.ReactNode }) => (
        <h2 className="text-xl font-semibold mt-5 mb-2">{children}</h2>
      ),
      h3: ({ children }: { children?: React.ReactNode }) => (
        <h3 className="text-lg font-medium mt-4 mb-2">{children}</h3>
      ),
      li: ({ children }: { children?: React.ReactNode }) => {
        const text = extractText(children);
        const key = text.slice(0, 60).trim();
        const sentences = paragraphMap.get(key);
        const hasInfo = sentences && sentences.some((s) => s.fact_count > 0 || s.node_ids.length > 0);
        const isSelected = selectedParaKey === key;

        return (
          <li
            className={`mb-1 ${
              isSelected
                ? "bg-primary/5 rounded px-1"
                : hasInfo
                  ? "hover:bg-muted/30 cursor-pointer rounded px-1"
                  : ""
            }`}
            onClick={
              hasInfo && sentences
                ? (e) => {
                    e.stopPropagation();
                    handleParaClick(key, sentences);
                  }
                : undefined
            }
          >
            {children}
          </li>
        );
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedParaKey, paragraphMap]
  );

  const hasDefinition = document.definition && document.definition.trim();

  return (
    <div className="flex gap-6">
      {/* Main document */}
      <div className="flex-1 min-w-0">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <CardTitle className="text-2xl">{document.concept}</CardTitle>
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
            {document.created_at && (
              <p className="text-sm text-muted-foreground">
                Created {new Date(document.created_at).toLocaleDateString()}
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

      {/* Right panel — paragraph/sentence details */}
      {selectedSentences && selectedSentences.length > 0 && (
        <div className="w-96 shrink-0">
          <Card className="sticky top-4">
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <CardTitle className="text-sm">
                {selectedSentences.length} sentence{selectedSentences.length !== 1 ? "s" : ""} in this section
              </CardTitle>
              <Button
                variant="ghost"
                size="icon"
                className="size-6"
                onClick={() => {
                  setSelectedSentences(null);
                  setSelectedParaKey(null);
                  setFactLinks(null);
                  setActiveSentencePos(null);
                }}
              >
                <X className="size-4" />
              </Button>
            </CardHeader>
            <CardContent className="space-y-3 max-h-[70vh] overflow-y-auto">
              {selectedSentences.map((sentence) => {
                const isActive = activeSentencePos === sentence.position;
                const hasNodes = sentence.node_ids.length > 0;
                const hasFacts = sentence.fact_count > 0;

                return (
                  <div
                    key={sentence.position}
                    className={`rounded-md border p-3 space-y-2 transition-colors ${
                      isActive ? "border-primary bg-primary/5" : "hover:bg-muted/30 cursor-pointer"
                    }`}
                    onClick={() => handleSentenceFactLoad(sentence)}
                  >
                    {/* Sentence preview */}
                    <p className="text-xs text-muted-foreground line-clamp-2">
                      {sentence.text.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").slice(0, 120)}
                      {sentence.text.length > 120 ? "..." : ""}
                    </p>

                    {/* Counts */}
                    <div className="flex gap-2">
                      {hasNodes && (
                        <Badge variant="outline" className="text-[10px]">
                          {sentence.node_ids.length} node{sentence.node_ids.length !== 1 ? "s" : ""}
                        </Badge>
                      )}
                      {hasFacts && (
                        <Badge variant="outline" className="text-[10px]">
                          {sentence.fact_count} fact{sentence.fact_count !== 1 ? "s" : ""}
                        </Badge>
                      )}
                    </div>

                    {/* Expanded details when active */}
                    {isActive && (
                      <div className="space-y-3 pt-2 border-t">
                        {/* Nodes */}
                        {hasNodes && (
                          <div className="space-y-1">
                            <h5 className="text-[10px] font-medium flex items-center gap-1">
                              <CircleDot className="size-3" /> Nodes
                            </h5>
                            {sentence.node_ids.map((nid) => {
                              const nodeInfo = nodeMap.get(nid);
                              return (
                                <Link
                                  key={nid}
                                  href={`/nodes/${nid}`}
                                  className="flex items-center gap-1.5 text-[11px] text-primary hover:underline"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <CircleDot className="size-2.5 shrink-0" />
                                  {nodeInfo?.concept ?? nid.slice(0, 8) + "..."}
                                </Link>
                              );
                            })}
                          </div>
                        )}

                        {/* Facts (lazy-loaded) */}
                        {hasFacts && (
                          <div className="space-y-1">
                            <h5 className="text-[10px] font-medium flex items-center gap-1">
                              <FileText className="size-3" /> Facts
                            </h5>
                            {loadingFacts ? (
                              <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
                                <Loader2 className="size-3 animate-spin" />
                                Loading...
                              </div>
                            ) : factLinks && factLinks.length > 0 ? (
                              factLinks.map((fl) => (
                                <Link
                                  key={fl.fact_id}
                                  href={`/facts/${fl.fact_id}`}
                                  className="flex items-center gap-1.5 text-[11px] text-primary hover:underline"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <FileText className="size-2.5 shrink-0" />
                                  {fl.fact_id.slice(0, 12)}...
                                  <span className="text-muted-foreground">
                                    ({(fl.distance * 100).toFixed(0)}%)
                                  </span>
                                </Link>
                              ))
                            ) : (
                              <p className="text-[10px] text-muted-foreground">No facts.</p>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────

/** Recursively extract plain text from React children. */
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
