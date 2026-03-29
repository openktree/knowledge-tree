import type { Config } from "@docusaurus/types";
import type * as Preset from "@docusaurus/preset-classic";

const config: Config = {
  title: "Knowledge Tree",
  tagline: "Open Knowledge Commons for Humanity",
  favicon: "img/favicon.ico",
  url: "https://docs.openktree.com",
  baseUrl: "/",
  organizationName: "openktree",
  projectName: "knowledge-tree",
  onBrokenLinks: "throw",
  onBrokenMarkdownLinks: "warn",

  i18n: {
    defaultLocale: "en",
    locales: ["en"],
  },

  presets: [
    [
      "classic",
      {
        docs: {
          routeBasePath: "/",
          sidebarPath: "./sidebars.ts",
          editUrl:
            "https://github.com/openktree/knowledge-tree/tree/main/docs-site/",
        },
        blog: false,
        theme: {
          customCss: "./src/css/custom.css",
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      defaultMode: "light",
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: "Knowledge Tree",
      logo: {
        alt: "Knowledge Tree",
        src: "img/logo.svg",
      },
      style: "dark",
      items: [
        {
          type: "docSidebar",
          sidebarId: "howItWorks",
          label: "How It Works",
          position: "left",
        },
        {
          type: "docSidebar",
          sidebarId: "mcp",
          label: "MCP",
          position: "left",
        },
        {
          type: "docSidebar",
          sidebarId: "contributing",
          label: "Contributing",
          position: "left",
        },
        {
          href: "https://openktree.com",
          label: "Home",
          position: "right",
        },
        {
          href: "https://research.openktree.com",
          label: "Research",
          position: "right",
        },
        {
          href: "https://wiki.openktree.com",
          label: "Wiki",
          position: "right",
        },
        {
          href: "https://github.com/openktree/knowledge-tree",
          label: "GitHub",
          position: "right",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "Documentation",
          items: [
            {
              label: "How It Works",
              to: "/how-it-works/values-and-principles",
            },
            { label: "MCP Integration", to: "/mcp/overview" },
            {
              label: "Contributing",
              to: "/contributing/architecture-overview",
            },
          ],
        },
        {
          title: "Services",
          items: [
            { label: "Home", href: "https://openktree.com" },
            { label: "Research App", href: "https://research.openktree.com" },
            { label: "Wiki", href: "https://wiki.openktree.com" },
            { label: "MCP Server", href: "https://mcp.openktree.com" },
          ],
        },
        {
          title: "Community",
          items: [
            {
              label: "GitHub",
              href: "https://github.com/openktree/knowledge-tree",
            },
            {
              label: "Contributing Guide",
              to: "/contributing/architecture-overview",
            },
          ],
        },
      ],
      copyright: "Knowledge Tree — Open Knowledge Commons for Humanity",
    },
    prism: {
      additionalLanguages: ["python", "bash", "json", "typescript", "yaml"],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
