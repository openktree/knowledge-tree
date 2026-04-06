"""Reusable prompt fragments for AI-generated content.

All prompts that instruct LLMs to produce links to nodes or facts should
import their link instructions from here so the format is defined in one
place.
"""

# ── Fact citation instruction (for prompts that provide {fact:uuid|label} tokens) ─

CITE_FACTS_INSTRUCTION = """\
When citing a specific piece of evidence in your analysis, embed a fact \
citation link. Each fact ends with a {fact:<uuid>|<label>} token. Cite it \
using a markdown link: [concise description](/facts/<uuid>). The link text \
must be a brief descriptive phrase (3-8 words) summarising the claim — \
never use index numbers, "fact 1", or generic text like "this source" or \
"here". Embed 3-8 citations naturally in your prose where you reference \
specific evidence. Do NOT reference facts by their index number. \
IMPORTANT: Your output must use markdown link syntax \
[description](/facts/<uuid>), NOT the {fact:uuid|label} token format \
shown in the fact list."""

# ── Node + fact linking instruction (for synthesis / long-form output) ──

LINK_NODES_AND_FACTS_INSTRUCTION = """\
## Linking Nodes & Facts

Your answer will be rendered as markdown. You MUST embed links to the \
nodes and facts you reference so the reader can drill into the details.

- **Node links** — When you mention a concept that corresponds to a \
node, link it on first mention using: `[concept name](/nodes/<node-uuid>)`. \
Example: `[Moon Formation](/nodes/a1b2c3...)`. Link each node only once — \
the first time it appears naturally in the text.

- **Fact links** — When citing a specific piece of evidence, create a \
markdown link using its UUID: `[short description](/facts/<fact-uuid>)`. \
The link text MUST be a short, descriptive phrase (5-10 words) — NEVER \
use generic text like "source", "here", or "link". \
Example: `[NASA confirmed water ice on lunar poles](/facts/d4e5f6...)`. \
Link the most important facts — aim for 2-5 per section, not every one. \
IMPORTANT: Always use markdown link syntax [text](/path/uuid), never \
raw {fact:uuid} tokens.

- **Do not over-link** — Link nodes on first mention and key facts \
that support critical claims. Plain text is fine for general analysis \
and transitions. The goal is navigability, not a wall of blue text."""

# ── Definition preservation instruction (for prompts that synthesize from dimensions) ──

PRESERVE_LINKS_INSTRUCTION = """\
The input dimensions may contain fact citation links like \
[description](/facts/<uuid>). Preserve the most important of these in \
your output — especially for specific measurements, key claims, and \
disputed points. Do not invent new /facts/ links; only carry forward \
links that appear in the input dimensions."""
