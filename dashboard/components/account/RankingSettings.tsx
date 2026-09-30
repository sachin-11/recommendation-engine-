"use client";

import * as React from "react";
import { Loader2, Save } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox, Slider } from "@/components/ui/controls";
import { Label } from "@/components/ui/label";
import { toastApiError } from "@/lib/api";
import { useUpdateDomainConfig } from "@/lib/hooks/account";
import { can, needsRole } from "@/lib/roles";
import type { RankingConfig, Tenant } from "@/types";

// The API's defaults (app/schemas/tenant.py RankingConfig); filled in for configs saved
// before the ranking section existed.
const DEFAULTS: Required<RankingConfig> = {
  enabled: true,
  engagement: 0.5,
  conversion: 0.5,
  negative: 0.5,
  popularity: 0,
  personalization: 0.2,
  control_share: 0,
};

type NumericKey = Exclude<keyof RankingConfig, "enabled">;

const FIELDS: { key: NumericKey; label: string; hint: string; max: number; percent?: boolean }[] = [
  { key: "engagement", label: "Engagement", hint: "Clicks and thumbs up per impression", max: 5 },
  { key: "conversion", label: "Conversion", hint: "Purchases and applies per impression", max: 5 },
  { key: "negative", label: "Negative", hint: "Thumbs down and ignores push items down", max: 5 },
  { key: "popularity", label: "Popularity", hint: "Favours items shown often", max: 5 },
  {
    key: "personalization",
    label: "Personalization",
    hint: "With a user_id, lean toward items that user liked",
    max: 1,
    percent: true,
  },
];

function display(value: number, percent?: boolean) {
  return percent ? `${Math.round(value * 100)}%` : value.toFixed(1);
}

export function RankingSettings({ tenant }: { tenant: Tenant }) {
  const saved = React.useMemo(
    () => ({ ...DEFAULTS, ...tenant.domain_config.ranking }),
    [tenant.domain_config.ranking],
  );
  const [ranking, setRanking] = React.useState(saved);
  const save = useUpdateDomainConfig();
  const dirty = JSON.stringify(ranking) !== JSON.stringify(saved);
  const canEdit = can(tenant, "ADMIN");
  const set = (patch: Partial<RankingConfig>) => setRanking((current) => ({ ...current, ...patch }));

  const onSave = () =>
    save.mutate(
      { ...tenant.domain_config, ranking },
      {
        onSuccess: () => toast.success("Ranking saved", { description: "New queries use it straight away." }),
        onError: (error) => toastApiError(error, "Could not save ranking"),
      },
    );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Ranking</CardTitle>
        <CardDescription>
          How user feedback reorders results. Similarity to the query counts 1; each weight scales how far an
          item&apos;s rate is above or below your average. Items without feedback keep their similarity order.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="flex items-start gap-3">
          <Checkbox
            id="ranking-enabled"
            checked={ranking.enabled}
            onCheckedChange={(checked) => set({ enabled: checked === true })}
            disabled={!canEdit}
          />
          <div className="space-y-1">
            <Label htmlFor="ranking-enabled">Use feedback and personalization</Label>
            <p className="text-xs text-muted-foreground">Off orders every result by similarity alone.</p>
          </div>
        </div>

        <fieldset className="grid gap-5 sm:grid-cols-2" disabled={!ranking.enabled || !canEdit}>
          {FIELDS.map(({ key, label, hint, max, percent }) => (
            <div key={key} className="space-y-2">
              <div className="flex items-baseline justify-between gap-2">
                <Label htmlFor={`ranking-${key}`}>{label}</Label>
                <span className="text-sm tabular-nums text-muted-foreground">{display(ranking[key], percent)}</span>
              </div>
              <Slider
                id={`ranking-${key}`}
                min={0}
                max={max}
                step={max === 1 ? 0.05 : 0.1}
                value={[ranking[key]]}
                onValueChange={([v]) => set({ [key]: Math.round(v * 100) / 100 })}
                aria-label={label}
                disabled={!ranking.enabled || !canEdit}
              />
              <p className="text-xs text-muted-foreground">{hint}</p>
            </div>
          ))}
        </fieldset>

        <div className="space-y-2 rounded-lg border p-4">
          <div className="flex items-baseline justify-between gap-2">
            <Label htmlFor="ranking-control_share">A/B test: control share</Label>
            <span className="text-sm tabular-nums text-muted-foreground">
              {ranking.control_share > 0 ? display(ranking.control_share, true) : "Off"}
            </span>
          </div>
          <Slider
            id="ranking-control_share"
            min={0}
            max={1}
            step={0.05}
            value={[ranking.control_share]}
            onValueChange={([v]) => set({ control_share: Math.round(v * 100) / 100 })}
            aria-label="Control share"
            disabled={!ranking.enabled || !canEdit}
          />
          <p className="text-xs text-muted-foreground">
            This share of traffic is ordered by similarity alone, so Analytics can show whether feedback ranking gets
            more clicks and conversions. A user with a <code className="font-mono">user_id</code> always gets the
            same variant.
          </p>
        </div>
      </CardContent>
      <CardFooter className="flex flex-wrap justify-end gap-2">
        {dirty && (
          <Button variant="ghost" onClick={() => setRanking(saved)} disabled={save.isPending}>
            Discard changes
          </Button>
        )}
        <Button
          onClick={onSave}
          disabled={!dirty || save.isPending || !canEdit}
          title={canEdit ? undefined : needsRole("ADMIN")}
        >
          {save.isPending ? <Loader2 className="animate-spin" /> : <Save />}
          Save ranking
        </Button>
      </CardFooter>
    </Card>
  );
}
