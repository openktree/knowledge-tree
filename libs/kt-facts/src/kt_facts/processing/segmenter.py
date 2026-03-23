import re

# Default chunk threshold: ~1 page (~3000 chars / ~750 tokens).
# Small chunks improve extraction precision — the model focuses on fewer
# paragraphs at a time, reducing missed facts and hallucinated connections.
# Brave/Serper snippets (200-400 chars) always fit in a single call.
DEFAULT_MAX_CHUNK = 3_000


def chunk_if_needed(text: str, max_chunk: int = DEFAULT_MAX_CHUNK) -> list[str]:
    """Split text into chunks only if it exceeds max_chunk characters.

    For most web search results (200-400 chars), this returns the text as-is
    in a single-element list. Only large sources get chunked.

    Strategy for large texts:
    1. Split on double newlines (paragraph boundaries).
    2. Merge paragraphs into chunks up to max_chunk.
    3. If a single paragraph exceeds max_chunk, split on sentence boundaries.

    Args:
        text: The source text to potentially chunk.
        max_chunk: Maximum characters per chunk.

    Returns:
        List of text chunks (always at least one if text is non-empty).
    """
    if not text or not text.strip():
        return []

    text = text.strip()

    # Fast path: text fits in a single chunk
    if len(text) <= max_chunk:
        return [text]

    # Split on double newlines (paragraph boundaries)
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current_chunk = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        if len(paragraph) > max_chunk:
            # Flush current chunk
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""
            # Split oversized paragraph on sentence boundaries
            chunks.extend(_split_on_sentences(paragraph, max_chunk))
        elif not current_chunk:
            current_chunk = paragraph
        elif len(current_chunk) + 2 + len(paragraph) <= max_chunk:
            current_chunk = current_chunk + "\n\n" + paragraph
        else:
            chunks.append(current_chunk)
            current_chunk = paragraph

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _split_on_sentences(text: str, max_length: int) -> list[str]:
    """Split text on sentence boundaries ('. ', '? ', '! ')."""
    sentences = re.split(r"(?<=[.?!])\s+", text)
    result: list[str] = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        if not current:
            current = sentence
        elif len(current) + 1 + len(sentence) <= max_length:
            current = current + " " + sentence
        else:
            if current:
                result.append(current)
            current = sentence

    if current:
        result.append(current)

    return result


# Keep the old function name as an alias for backwards compatibility in tests
def segment_text(text: str, max_segment_length: int = 1000) -> list[str]:
    """Legacy function — splits text into segments.

    Kept for backwards compatibility. New code should use chunk_if_needed().
    """
    return chunk_if_needed(text, max_chunk=max_segment_length)
