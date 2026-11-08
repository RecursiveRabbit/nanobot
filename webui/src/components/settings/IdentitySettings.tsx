import { Check, Loader2, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchIdentity, updateIdentity } from "@/lib/api";
import {
  cacheProfileOriginator,
  loadCachedOriginator,
  loadProfileClientId,
} from "@/lib/identity";
import { useClient } from "@/providers/ClientProvider";

/**
 * Settings → Identity: the operator-selected display name for this WebUI
 * profile. Server-persisted (identity.json), applied to NEW messages from
 * this profile at the harness seam. Attribution only — never a permission,
 * never an authentication factor. Empty stays explicitly anonymous; clearing
 * never rewrites history.
 */
export function IdentitySettings() {
  const { t } = useTranslation();
  const { client } = useClient();
  const [clientId] = useState(() => loadProfileClientId());
  const [name, setName] = useState("");
  const [loadedName, setLoadedName] = useState<string | null>(null);
  const [status, setStatus] = useState<"idle" | "saving">("idle");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!clientId) return;
    let cancelled = false;
    fetchIdentity(client, clientId)
      .then((payload) => {
        if (cancelled) return;
        setName(payload.display_name);
        setLoadedName(payload.display_name);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [client, clientId]);

  const applyLocalOriginator = useCallback((displayName: string) => {
    cacheProfileOriginator(
      displayName.trim()
        ? { kind: "human", label: displayName.trim(), attestation: "operator-profile" }
        : { kind: "anonymous" },
    );
  }, []);

  const save = useCallback(
    async (displayName: string) => {
      if (!clientId) return;
      setStatus("saving");
      setNotice(null);
      setError(null);
      try {
        const payload = await updateIdentity(client, clientId, displayName.trim());
        setName(payload.display_name);
        setLoadedName(payload.display_name);
        applyLocalOriginator(payload.display_name);
        setNotice(
          payload.display_name
            ? t("settings.identity.noticeSaved")
            : t("settings.identity.noticeCleared"),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setStatus("idle");
      }
    },
    [client, clientId, applyLocalOriginator, t],
  );

  const dirty = loadedName !== null && name.trim() !== loadedName.trim();

  return (
    <section className="settings-stack" data-testid="identity-settings">
      <div className="settings-section-heading">
        <h2 className="text-[15px] font-medium">{t("settings.identity.title")}</h2>
      </div>
      <p className="text-[13px] text-muted-foreground">
        {t("settings.identity.description")}
      </p>

      {!clientId ? (
        <p className="text-[13px] text-muted-foreground" role="status">
          {t("settings.identity.unavailable")}
        </p>
      ) : (
        <>
          <div className="flex max-w-md flex-col gap-2">
            <label
              htmlFor="identity-display-name"
              className="text-[13px] font-medium"
            >
              {t("settings.identity.label")}
            </label>
            <Input
              id="identity-display-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t("settings.identity.placeholder")}
              aria-label={t("settings.identity.label")}
              maxLength={64}
              disabled={status === "saving"}
            />
            <p className="text-[12px] text-muted-foreground">
              {t("settings.identity.hint")}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Button
              type="button"
              onClick={() => void save(name)}
              disabled={status === "saving" || !dirty}
              data-testid="identity-save"
            >
              {status === "saving" ? (
                <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : null}
              {t("settings.identity.save")}
            </Button>
            {loadedName ? (
              <Button
                type="button"
                variant="ghost"
                onClick={() => void save("")}
                disabled={status === "saving"}
                data-testid="identity-clear"
              >
                <XCircle className="mr-2 h-3.5 w-3.5" aria-hidden />
                {t("settings.identity.clear")}
              </Button>
            ) : null}
          </div>

          {notice ? (
            <p className="flex items-center gap-1.5 text-[13px] text-muted-foreground" role="status">
              <Check className="h-3.5 w-3.5 text-emerald-600" aria-hidden />
              {notice}
            </p>
          ) : null}
          {error ? (
            <p className="text-[13px] text-destructive" role="alert">
              {error}
            </p>
          ) : null}

          <p className="text-[12px] text-muted-foreground">
            {t("settings.identity.currentState", {
              state:
                loadedName?.trim()
                  ? t("settings.identity.stateNamed", { name: loadedName.trim() })
                  : t("settings.identity.stateAnonymous"),
            })}
          </p>
        </>
      )}
    </section>
  );
}

/** Optimistic originator used for locally echoed user messages until the
 * server's authoritative ``user_message`` broadcast arrives. */
export function optimisticOriginator(): ReturnType<typeof loadCachedOriginator> {
  return loadCachedOriginator();
}
