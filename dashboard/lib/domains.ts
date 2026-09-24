import { Briefcase, GraduationCap, type LucideIcon, Settings2, ShoppingBag, UtensilsCrossed } from "lucide-react";

import type { DomainConfig, DomainType } from "@/types";

export interface DomainPreset {
  type: DomainType;
  label: string;
  icon: LucideIcon;
  useCase: string;
  config: DomainConfig;
  exampleItem: Record<string, unknown>;
  exampleQuery: string;
}

// Keep in sync with DEFAULT_DOMAIN_CONFIGS in app/schemas/tenant.py.
export const DOMAIN_PRESETS: DomainPreset[] = [
  {
    type: "HR",
    label: "HR & Hiring",
    icon: Briefcase,
    useCase: "Match candidates to jobs and suggest similar roles",
    config: {
      primary_embedding_field: "description",
      searchable_fields: ["title", "description", "skills"],
      filter_fields: ["location", "department", "employment_type"],
      item_label: "job",
    },
    exampleItem: {
      external_id: "job-101",
      title: "Senior Python Developer",
      description: "Build FastAPI microservices for a hiring platform",
      skills: ["Python", "FastAPI", "PostgreSQL"],
      location: "Bangalore",
      department: "Engineering",
      employment_type: "full_time",
    },
    exampleQuery: "senior python developer with fastapi experience",
  },
  {
    type: "FOOD",
    label: "Food & Delivery",
    icon: UtensilsCrossed,
    useCase: "Suggest similar dishes and match cravings",
    config: {
      primary_embedding_field: "description",
      searchable_fields: ["name", "description", "cuisine", "ingredients"],
      filter_fields: ["cuisine", "dietary_tags", "price_range"],
      item_label: "dish",
    },
    exampleItem: {
      external_id: "dish-1",
      name: "Penne Arrabbiata",
      description: "Penne in a fiery tomato, garlic and red chilli sauce",
      cuisine: "Italian",
      ingredients: ["penne", "tomato", "chilli"],
      dietary_tags: ["vegetarian"],
      price_range: "$$",
    },
    exampleQuery: "spicy vegetarian pasta",
  },
  {
    type: "ECOMMERCE",
    label: "E-commerce",
    icon: ShoppingBag,
    useCase: "Show related products and power semantic search",
    config: {
      primary_embedding_field: "description",
      searchable_fields: ["title", "description", "brand", "tags"],
      filter_fields: ["category", "brand", "price_range"],
      item_label: "product",
    },
    exampleItem: {
      external_id: "sku-2231",
      title: "WH-1000XM5 Headphones",
      description: "Wireless over-ear headphones with adaptive noise cancelling",
      brand: "Sony",
      tags: ["wireless", "anc", "bluetooth"],
      category: "audio",
      price_range: "$$$",
    },
    exampleQuery: "wireless noise cancelling headphones",
  },
  {
    type: "EDTECH",
    label: "EdTech",
    icon: GraduationCap,
    useCase: "Recommend related courses and learning paths",
    config: {
      primary_embedding_field: "description",
      searchable_fields: ["title", "description", "topics"],
      filter_fields: ["level", "category", "language"],
      item_label: "course",
    },
    exampleItem: {
      external_id: "course-7",
      title: "Machine Learning Foundations",
      description: "Linear models, trees and evaluation, taught with Python",
      topics: ["ml", "python", "statistics"],
      level: "beginner",
      category: "data-science",
      language: "en",
    },
    exampleQuery: "beginner machine learning with python",
  },
  {
    type: "CUSTOM",
    label: "Custom",
    icon: Settings2,
    useCase: "Describe your own items and fields",
    config: {
      primary_embedding_field: "description",
      searchable_fields: ["title", "description"],
      filter_fields: ["category"],
      item_label: "item",
    },
    exampleItem: {
      external_id: "item-1",
      title: "Your item title",
      description: "The main text that describes this item",
      category: "example",
    },
    exampleQuery: "describe what you are looking for",
  },
];

export function presetFor(type: DomainType): DomainPreset {
  return DOMAIN_PRESETS.find((preset) => preset.type === type) ?? DOMAIN_PRESETS[4];
}

/** An example item shaped by `config`: preset values where the field names match. */
export function exampleItemFor(config: DomainConfig, type: DomainType): Record<string, unknown> {
  const preset = presetFor(type).exampleItem;
  const fields = Array.from(
    new Set([config.primary_embedding_field, ...config.searchable_fields, ...config.filter_fields]),
  );
  const item: Record<string, unknown> = { external_id: preset.external_id ?? "item-1" };
  for (const field of fields) {
    if (field) item[field] = preset[field] ?? `<${field}>`;
  }
  return item;
}
