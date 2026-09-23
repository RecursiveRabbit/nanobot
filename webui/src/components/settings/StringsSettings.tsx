import { RotateCcw, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { SettingsTextEditor } from "@/components/settings/shared/SettingsTextEditor";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  fetchStringsCatalog,
  updateStringOverride,
  type StringCatalogEntry,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

/**
 * Every string the harness can inject into model context, editable by the
 * operator. Defaults ship with the code; overrides live in config.json and
 * apply live. The model is never told which is which.
 */
export function StringsSettings() {
  const { t } = useTranslation();
  const { client, token } = useClient();
  const [entries, setEntries] = useState<StringCatalogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setEntries((await fetchStringsCatalog(token)).strings);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(
    async (key: string, value: string | null) => {
      const payload = await updateStringOverride(client, key, value);
      setEntries(payload.strings);
    },
    [client],
  );

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const visible = (entries ?? []).filter((entry) => {
      if (!showAdvanced && entry.advanced) return false;
      if (!needle) return true;
      return (
        entry.key.toLowerCase().includes(needle) ||
        entry.effective.toLowerCase().includes(needle)
      );
    });
    const byGroup = new Map<string, StringCatalogEntry[]>();
    for (const entry of visible) {
      const bucket = byGroup.get(entry.group) ?? [];
      bucket.push(entry);
      byGroup.set(entry.group, bucket);
    }
    return [...byGroup.entries()];
  }, [entries, query, showAdvanced]);

  const advancedCount = useMemo(
    () => (entries ?? []).filter((entry) => entry.advanced).length,
    [entries],
  );

  return (
    <section className="settings-stack" data-testid="strings-settings">
      <div className="settings-section-heading">
        <h2 className="text-[15px] font-medium">{t("stringsPage.title")}</h2>
      </div>
      <p className="text-[13px] text-muted-foreground">{t("stringsPage.intro")}</p>

      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("stringsPage.search")}
          aria-label={t("stringsPage.search")}
          className="pl-8"
        />
      </div>

      {error ? (
        <div className="text-[13px] text-destructive">{error}</div>
      ) : null}
      {!entries && !error ? (
        <div className="py-6 text-[13px] text-muted-foreground">
          {t("stringsPage.loading")}
        </div>
      ) : null}

      {groups.map(([group, items]) => (
        <div key={group} className="flex flex-col gap-2">
          <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {group}
          </div>
          {items.map((entry) => (
            <StringRow key={entry.key} entry={entry} onSave={save} />
          ))}
        </div>
      ))}

      {advancedCount > 0 ? (
        <Button
          type="button"
          variant="ghost"
          onClick={() => setShowAdvanced((value) => !value)}
          className="self-start text-muted-foreground"
          aria-expanded={showAdvanced}
        >
          {showAdvanced
            ? t("stringsPage.hideAdvanced")
            : t("stringsPage.showAdvanced", { count: advancedCount })}
        </Button>
      ) : null}
    </section>
  );
}

function StringRow({
  entry,
  onSave,
}: {
  entry: StringCatalogEntry;
  onSave: (key: string, value: string | null) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [resetting, setResetting] = useState(false);

  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg border border-border/60 p-3",
        entry.overridden && "border-amber-500/40",
      )}
      data-testid={`string-row-${entry.key}`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <code className="text-[12px] font-medium">{entry.key}</code>
          {entry.overridden ? (
            <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
              {t("stringsPage.overridden")}
            </span>
          ) : null}
        </div>
        <pre className="mt-1 max-h-24 overflow-hidden whitespace-pre-wrap break-words font-sans text-[12px]/[1.45] text-muted-foreground">
          {entry.effective}
        </pre>
      </div>
      {entry.overridden ? (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={resetting}
          aria-label={t("stringsPage.reset")}
          title={t("stringsPage.reset")}
          onClick={async () => {
            setResetting(true);
            try {
              await onSave(entry.key, null);
            } finally {
              setResetting(false);
            }
          }}
          className="h-9 w-9 shrink-0 rounded-full text-muted-foreground"
        >
          <RotateCcw className="h-4 w-4" aria-hidden />
        </Button>
      ) : null}
      <SettingsTextEditor
        title={entry.key}
        description={entry.overridden ? t("stringsPage.editingOverride") : t("stringsPage.editingDefault")}
        value={entry.effective}
        onSave={async (value) => {
          await onSave(entry.key, value);
        }}
      />
    </div>
  );
}
