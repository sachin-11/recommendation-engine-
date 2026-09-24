import type { ReactNode } from "react";

import Link from "@docusaurus/Link";
import useDocusaurusContext from "@docusaurus/useDocusaurusContext";
import Layout from "@theme/Layout";

const FEATURES = [
  {
    title: "Any catalogue",
    body: "Jobs, dishes, products, courses or your own items. A domain config says which fields describe an item and which are filters.",
  },
  {
    title: "Three ways to ask",
    body: "Recommend by free text, by an item you already have (“similar jobs”), or by a profile such as a candidate’s skills.",
  },
  {
    title: "Production ready",
    body: "Retries, rate limits, caching, per-tenant isolation, analytics and feedback collection, with SDKs for JavaScript and Python.",
  },
];

export default function Home(): ReactNode {
  const { siteConfig } = useDocusaurusContext();
  return (
    <Layout title="Documentation" description={siteConfig.tagline}>
      <header className="hero hero--reco">
        <div className="container">
          <h1 className="hero__title">RecoEngine</h1>
          <p className="hero__subtitle">{siteConfig.tagline}</p>
          <div className="buttons">
            <Link className="button button--primary button--lg" to="/docs/getting-started/quickstart">
              Quickstart
            </Link>
            <Link className="button button--secondary button--lg" to="/api-reference/">
              API reference
            </Link>
          </div>
        </div>
      </header>
      <main className="container">
        <section className="features">
          {FEATURES.map((feature) => (
            <div key={feature.title}>
              <h3>{feature.title}</h3>
              <p>{feature.body}</p>
            </div>
          ))}
        </section>
      </main>
    </Layout>
  );
}
