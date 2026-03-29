import type { SidebarsConfig } from "@docusaurus/plugin-content-docs";

const sidebars: SidebarsConfig = {
  howItWorks: [
    {
      type: "category",
      label: "How It Works",
      collapsed: false,
      items: [
        "how-it-works/values-and-principles",
        "how-it-works/facts",
        "how-it-works/entity-concept-extraction",
        "how-it-works/seeds-and-routing",
        "how-it-works/relations-and-edges",
        "how-it-works/dimensions",
        "how-it-works/synthesis-and-super-synthesis",
      ],
    },
  ],
  mcp: [
    {
      type: "category",
      label: "MCP Integration",
      collapsed: false,
      items: [
        "mcp/overview",
        "mcp/connecting",
        "mcp/available-tools",
        "mcp/examples",
      ],
    },
  ],
  contributing: [
    {
      type: "category",
      label: "Contributing",
      collapsed: false,
      items: [
        "contributing/architecture-overview",
        "contributing/core-objects",
        "contributing/services",
        "contributing/libraries",
        "contributing/development-setup",
      ],
    },
  ],
};

export default sidebars;
