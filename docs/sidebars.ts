import type { SidebarsConfig } from "@docusaurus/plugin-content-docs";

const sidebars: SidebarsConfig = {
  docs: [
    {
      type: "category",
      label: "Getting started",
      collapsed: false,
      items: [
        "getting-started/introduction",
        "getting-started/quickstart",
        "getting-started/authentication",
      ],
    },
    {
      type: "category",
      label: "Domain guides",
      collapsed: false,
      items: [
        "guides/hr-domain",
        "guides/food-domain",
        "guides/ecommerce-domain",
        "guides/custom-domain",
      ],
    },
    {
      type: "category",
      label: "SDKs",
      items: ["sdks/javascript", "sdks/python"],
    },
    { type: "link", label: "API reference", href: "/api-reference/" },
    "deployment",
  ],
};

export default sidebars;
