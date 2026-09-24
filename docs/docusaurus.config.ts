import type * as Preset from "@docusaurus/preset-classic";
import type { Config } from "@docusaurus/types";
import { themes as prismThemes } from "prism-react-renderer";

const config: Config = {
  title: "RecoEngine Docs",
  tagline: "Recommendations for any catalogue: upload items, get similar ones back.",
  favicon: "img/favicon.svg",

  url: "https://docs.recoengine.io",
  baseUrl: "/",

  organizationName: "sachin-11",
  projectName: "recommendation-engine-",

  onBrokenLinks: "throw",
  markdown: { hooks: { onBrokenMarkdownLinks: "throw" } },

  i18n: { defaultLocale: "en", locales: ["en"] },

  presets: [
    [
      "classic",
      {
        docs: {
          sidebarPath: "./sidebars.ts",
          editUrl: "https://github.com/sachin-11/recommendation-engine-/tree/main/docs/",
        },
        blog: false,
        theme: { customCss: "./src/css/custom.css" },
      } satisfies Preset.Options,
    ],
    [
      // API reference rendered from static/openapi.json (npm run openapi regenerates it).
      "redocusaurus",
      {
        specs: [{ id: "recoengine", spec: "static/openapi.json", route: "/api-reference/" }],
        theme: { primaryColor: "#4f46e5" },
      },
    ],
  ],

  themeConfig: {
    colorMode: { respectPrefersColorScheme: true },
    navbar: {
      title: "RecoEngine",
      logo: { alt: "RecoEngine", src: "img/logo.svg" },
      items: [
        { type: "docSidebar", sidebarId: "docs", position: "left", label: "Docs" },
        { to: "/api-reference/", label: "API Reference", position: "left" },
        { to: "/docs/sdks/javascript", label: "SDKs", position: "left" },
        {
          href: "https://github.com/sachin-11/recommendation-engine-",
          label: "GitHub",
          position: "right",
        },
      ],
    },
    footer: {
      style: "dark",
      links: [
        {
          title: "Docs",
          items: [
            { label: "Quickstart", to: "/docs/getting-started/quickstart" },
            { label: "API Reference", to: "/api-reference/" },
          ],
        },
        {
          title: "SDKs",
          items: [
            { label: "JavaScript / TypeScript", to: "/docs/sdks/javascript" },
            { label: "Python", to: "/docs/sdks/python" },
          ],
        },
        {
          title: "More",
          items: [{ label: "GitHub", href: "https://github.com/sachin-11/recommendation-engine-" }],
        },
      ],
      copyright: `© ${new Date().getFullYear()} RecoEngine. MIT licensed.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ["bash", "json", "python", "typescript"],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
