import type { SynthesisSentenceResponse } from "../types/index.js";

/**
 * Parse a synthesis concept like "Topic [20260326-171500]" into
 * a clean title and a human-readable date.
 */
export function formatSynthesisConcept(concept: string): {
  title: string;
  date: string | null;
} {
  const match = concept.match(
    /^(.+?)\s*\[(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})\]$/
  );
  if (!match) {
    return { title: concept, date: null };
  }
  const [, title, year, month, day, hour, minute] = match;
  const d = new Date(`${year}-${month}-${day}T${hour}:${minute}:00Z`);
  const date = d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  return { title: title.trim(), date };
}

/**
 * Build a map from paragraph plain-text keys (first 60 chars) to the
 * sentences that fall within that paragraph. Used for sentence-level
 * fact linking in synthesis views.
 */
export function buildParagraphSentenceMap(
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
    const lines = block
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    const isList =
      lines.length > 1 &&
      lines.every((l) => /^(\d+\.\s|[-*]\s)/.test(l));
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
