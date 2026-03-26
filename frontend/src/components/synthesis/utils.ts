/**
 * Parse a synthesis concept like "Topic [20260326-171500]" into
 * a clean title and a human-readable date.
 */
export function formatSynthesisConcept(concept: string): {
  title: string;
  date: string | null;
} {
  const match = concept.match(/^(.+?)\s*\[(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})\]$/);
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
