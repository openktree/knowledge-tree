/** A plain text segment of a justification string. */
export interface TextSegment {
  kind: "text";
  text: string;
}

/** A {fact:uuid} reference replaced with a citation number. */
export interface CitationSegment {
  kind: "citation";
  factId: string;
  num: number;
}

/** A markdown [text](url) link. */
export interface LinkSegment {
  kind: "link";
  text: string;
  href: string;
}

/** Bold text segment. */
export interface BoldSegment {
  kind: "bold";
  text: string;
}

export type JustificationSegment = TextSegment | CitationSegment;
export type RichTextSegment = TextSegment | CitationSegment | LinkSegment | BoldSegment;

/**
 * Convert a justification string to plain text, replacing {fact:uuid}
 * references with [N] citation numbers. Safe to use in title attributes.
 */
export function justificationTooltip(text: string | null | undefined): string | undefined {
  if (!text) return undefined;
  const segs = parseJustification(text);
  return segs.map((s) => (s.kind === "text" ? s.text : `[${s.num}]`)).join("");
}

/**
 * Parse a justification string containing {fact:uuid} references into
 * an array of text and citation segments. Citations are numbered [1], [2], …
 * in order of first appearance.
 */
export function parseJustification(
  text: string | null | undefined
): JustificationSegment[] {
  if (!text) return [];

  const segments: JustificationSegment[] = [];
  const seen = new Map<string, number>();
  let counter = 1;
  let remaining = text;

  const pattern = /\{fact:([0-9a-f-]+)\}/gi;
  let match: RegExpExecArray | null;
  let lastIndex = 0;

  pattern.lastIndex = 0;
  const str = text;

  while ((match = pattern.exec(str)) !== null) {
    const before = str.slice(lastIndex, match.index);
    if (before) segments.push({ kind: "text", text: before });

    const factId = match[1];
    if (!seen.has(factId)) {
      seen.set(factId, counter++);
    }
    segments.push({ kind: "citation", factId, num: seen.get(factId)! });
    lastIndex = match.index + match[0].length;
  }

  const tail = str.slice(lastIndex);
  if (tail) segments.push({ kind: "text", text: tail });

  return segments;
}

/**
 * Fix malformed markdown links produced by AI models.
 *
 * Handles both /facts and /nodes patterns:
 *   [label](/facts:uuid]  →  [label](/facts/uuid)   (colon + wrong bracket)
 *   [label](/facts/uuid]  →  [label](/facts/uuid)   (wrong closing bracket only)
 *   [label](/nodes:uuid]  →  [label](/nodes/uuid)   (same for nodes)
 */
export function sanitizeRichText(text: string): string {
  // Pass 1: [label](url] → [label](url)  (wrong closing bracket)
  let out = text.replace(
    /\[([^\]]+)\]\(([^)\]]*)\]/g,
    (_, label: string, url: string) => `[${label}](${url})`
  );
  // Pass 2: /facts:uuid or /nodes:uuid → /facts/uuid or /nodes/uuid  (colon → slash)
  out = out.replace(/\(\/(facts|nodes):([0-9a-f-]+)\)/gi, "(/$1/$2)");
  // Pass 3: [/facts/uuid] or [/facts:uuid] (bare bracket, no text portion) → {fact:uuid}
  out = out.replace(/\[\/facts[/:]([0-9a-f-]+)\]/gi, "{fact:$1}");
  // Pass 4: [/nodes/uuid] or [/nodes:uuid] (bare bracket, no text portion) → [node](/nodes/uuid)
  out = out.replace(/\[\/nodes[/:]([0-9a-f-]+)\]/gi, "[node](/nodes/$1)");
  return out;
}

/**
 * Parse rich text containing {fact:uuid} references, {{fact:uuid|label}}
 * references, and/or markdown links [text](url) into segments suitable
 * for rendering with clickable links.
 */
export function parseRichText(
  text: string | null | undefined
): RichTextSegment[] {
  if (!text) return [];
  text = sanitizeRichText(text);

  const segments: RichTextSegment[] = [];
  const seen = new Map<string, number>();
  let counter = 1;

  // Match {{fact:uuid|label}}, {fact:uuid}, **bold**, or [text](url) markdown links
  const pattern = /\{\{fact:([0-9a-f-]+)\|[^}]*\}\}|\{fact:([0-9a-f-]+)\}|\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*/gi;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    const before = text.slice(lastIndex, match.index);
    if (before) segments.push({ kind: "text", text: before });

    if (match[1]) {
      // {{fact:uuid|label}} format
      const factId = match[1];
      if (!seen.has(factId)) seen.set(factId, counter++);
      segments.push({ kind: "citation", factId, num: seen.get(factId)! });
    } else if (match[2]) {
      // {fact:uuid} format
      const factId = match[2];
      if (!seen.has(factId)) seen.set(factId, counter++);
      segments.push({ kind: "citation", factId, num: seen.get(factId)! });
    } else if (match[5]) {
      // **bold** format
      segments.push({ kind: "bold", text: match[5] });
    } else {
      segments.push({ kind: "link", text: match[3], href: match[4] });
    }
    lastIndex = match.index + match[0].length;
  }

  const tail = text.slice(lastIndex);
  if (tail) segments.push({ kind: "text", text: tail });

  return segments;
}

/** A block of parsed markdown content (header, paragraph, list item, or table). */
export interface MarkdownBlock {
  kind: "heading" | "paragraph" | "list-item" | "table";
  level?: number; // 1-6 for headings
  segments: RichTextSegment[];
  rawHtml?: string; // Pre-rendered HTML for table blocks
}

/** Check if a line looks like a markdown table row: | cell | cell | */
function isTableLine(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.length > 1;
}

/** Check if a line is the separator row: |---|---|---| */
function isTableSeparator(line: string): boolean {
  return /^\|[\s:?-]+(\|[\s:?-]+)*\|$/.test(line.trim());
}

/** Parse accumulated table lines into an HTML <table> string. */
function tableLinesToHtml(tableLines: string[]): string {
  const rows = tableLines
    .filter((l) => !isTableSeparator(l))
    .map((l) =>
      l
        .trim()
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((c) => c.trim())
    );
  if (rows.length === 0) return "";

  const headerCells = rows[0].map((c) => `<th>${c}</th>`).join("");
  const bodyRows = rows
    .slice(1)
    .map((row) => `<tr>${row.map((c) => `<td>${c}</td>`).join("")}</tr>`)
    .join("");

  return `<div class="table-scroll"><table><thead><tr>${headerCells}</tr></thead><tbody>${bodyRows}</tbody></table></div>`;
}

/**
 * Parse markdown text into blocks (headings, paragraphs, list items, and tables)
 * with rich text segments inside each block. Handles #/##/### headings, pipe
 * tables, and groups consecutive non-empty lines into paragraphs.
 */
export function parseMarkdownBlocks(
  text: string | null | undefined
): MarkdownBlock[] {
  if (!text) return [];

  const lines = text.split("\n");
  const blocks: MarkdownBlock[] = [];
  let paragraphLines: string[] = [];
  let tableLines: string[] = [];

  function flushParagraph() {
    if (paragraphLines.length === 0) return;
    const content = paragraphLines.join("\n").trim();
    if (content) {
      blocks.push({ kind: "paragraph", segments: parseRichText(content) });
    }
    paragraphLines = [];
  }

  function flushTable() {
    if (tableLines.length === 0) return;
    const rawHtml = tableLinesToHtml(tableLines);
    if (rawHtml) {
      blocks.push({ kind: "table", segments: [], rawHtml });
    }
    tableLines = [];
  }

  for (const line of lines) {
    if (isTableLine(line)) {
      flushParagraph();
      tableLines.push(line);
      continue;
    }

    if (tableLines.length > 0) {
      flushTable();
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
    const listMatch = line.match(/^(\s*)([-*]|\d+\.)\s+(.+)$/);
    if (headingMatch) {
      flushParagraph();
      const level = headingMatch[1].length;
      blocks.push({
        kind: "heading",
        level,
        segments: parseRichText(headingMatch[2]),
      });
    } else if (listMatch) {
      flushParagraph();
      blocks.push({
        kind: "list-item",
        segments: parseRichText(listMatch[3]),
      });
    } else if (line.trim() === "") {
      flushParagraph();
    } else {
      paragraphLines.push(line);
    }
  }
  flushTable();
  flushParagraph();

  return blocks;
}
