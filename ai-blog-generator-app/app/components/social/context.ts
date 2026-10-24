import { useRouteLoaderData } from "react-router";

import type { EngineSettings, FallbackModel, RuleInfo, SettingsResponse } from "../../lib/social-engine.types";
import { DEFAULT_TIMEZONE, safeTimezone } from "../../lib/social-time";
import { CONTENT_TYPE_FALLBACK } from "./vocab";

/** Data returned by the app/routes/app.social.tsx layout loader. */
export type SocialLayoutData = {
  settingsResponse: SettingsResponse | null;
  error: string;
};

export type SocialContext = {
  tz: string;
  settings: EngineSettings | null;
  contentTypes: Record<string, string>;
  providers: Record<string, { label: string; max_length: number }>;
  rules: Record<string, RuleInfo>;
  formats: Record<string, [number, number]>;
  fallbackModels: FallbackModel[];
  hasLogo: boolean;
  layoutError: string;
};

/** Store settings, timezone and vocabulary loaded once by the Social Autopilot layout. */
export function useSocial(): SocialContext {
  const data = useRouteLoaderData("routes/app.social") as SocialLayoutData | undefined;
  const response = data?.settingsResponse ?? null;
  return {
    tz: safeTimezone(response?.settings?.schedule?.timezone || DEFAULT_TIMEZONE),
    settings: response?.settings ?? null,
    contentTypes: response?.content_types && Object.keys(response.content_types).length ? response.content_types : CONTENT_TYPE_FALLBACK,
    providers: response?.providers ?? {},
    rules: response?.rules ?? {},
    formats: response?.formats ?? {},
    fallbackModels: response?.fallback_models ?? [],
    hasLogo: response?.has_logo ?? false,
    layoutError: data?.error ?? "",
  };
}
