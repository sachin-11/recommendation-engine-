"use client";

import { CopyButton } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { publicApiUrl } from "@/lib/utils";

function snippets(apiKey: string, item: Record<string, unknown>, query: string) {
  const API_BASE_URL = publicApiUrl();
  const items = JSON.stringify([item], null, 2);
  const indented = (text: string, pad: string) => text.split("\n").join(`\n${pad}`);
  return {
    curl: `# 1. Upload an item
curl -X POST ${API_BASE_URL}/api/v1/items/upload \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"async": true, "items": ${indented(items, "  ")}}'

# 2. Get recommendations
curl -X POST ${API_BASE_URL}/api/v1/recommend/by-text \\
  -H "X-API-Key: ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "${query}", "top_k": 5}'`,
    python: `import requests

API = "${API_BASE_URL}/api/v1"
HEADERS = {"X-API-Key": "${apiKey}"}

# 1. Upload an item
requests.post(f"{API}/items/upload", headers=HEADERS, json={
    "async": True,
    "items": ${indented(items, "    ")},
}).raise_for_status()

# 2. Get recommendations
response = requests.post(f"{API}/recommend/by-text", headers=HEADERS,
                         json={"query": "${query}", "top_k": 5})
for result in response.json()["results"]:
    print(result["rank"], result["external_id"], result["score"])`,
    node: `const API = "${API_BASE_URL}/api/v1";
const headers = { "X-API-Key": "${apiKey}", "Content-Type": "application/json" };

// 1. Upload an item
await fetch(\`\${API}/items/upload\`, {
  method: "POST",
  headers,
  body: JSON.stringify({ async: true, items: ${indented(items, "  ")} }),
});

// 2. Get recommendations
const res = await fetch(\`\${API}/recommend/by-text\`, {
  method: "POST",
  headers,
  body: JSON.stringify({ query: "${query}", top_k: 5 }),
});
const { results } = await res.json();
console.log(results);`,
  };
}

export function QuickStart({
  apiKey,
  exampleItem,
  exampleQuery,
}: {
  apiKey: string;
  exampleItem: Record<string, unknown>;
  exampleQuery: string;
}) {
  const code = snippets(apiKey, exampleItem, exampleQuery);
  const tabs = [
    { value: "curl", label: "curl" },
    { value: "python", label: "Python" },
    { value: "node", label: "Node.js" },
  ] as const;
  return (
    <Tabs defaultValue="curl">
      <TabsList>
        {tabs.map((tab) => (
          <TabsTrigger key={tab.value} value={tab.value}>
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {tabs.map((tab) => (
        <TabsContent key={tab.value} value={tab.value} className="relative">
          <CopyButton value={code[tab.value]} className="absolute right-2 top-2 h-8 w-8" label="Snippet copied" />
          <pre className="max-h-80 overflow-auto rounded-md border bg-zinc-950 p-4 pr-12 font-mono text-xs leading-relaxed text-zinc-100">
            <code>{code[tab.value]}</code>
          </pre>
        </TabsContent>
      ))}
    </Tabs>
  );
}
