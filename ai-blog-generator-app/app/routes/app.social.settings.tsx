import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { useActionData, useLoaderData } from "react-router";

import { SettingsSections } from "../components/social/SettingsSections";
import { useSocial } from "../components/social/context";
import { Banner } from "../components/social/ui";
import {
  SocialEngineError, formJson, formString, runIntent, seFetch, type Collection, type ProductsResponse,
} from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "Settings" };

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  // Settings themselves come from the layout loader; collections are needed for hashtag clouds.
  const catalogue = await seFetch<ProductsResponse>("/products", { query: { page_size: 1 } }).catch(() => null);
  return { collections: (catalogue?.collections || []) as Collection[] };
};

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  const section = formString(form, "section");
  return runIntent<{ section: string }>(intent, async () => {
    if (!intent.startsWith("save:")) throw new SocialEngineError(`Unknown action "${intent}".`);
    const patch = formJson<Record<string, unknown>>(form, "patch");
    if (!patch || typeof patch !== "object") throw new SocialEngineError("Nothing to save.");
    // Time zone, posting days and window are validated by the backend (exact message shown).
    await seFetch("/settings", { body: { patch } });
    return { message: "Settings saved.", data: { section } };
  });
};

export default function SettingsPage() {
  const { collections } = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { settings, providers, fallbackModels, layoutError } = useSocial();
  if (!settings) {
    return <Banner tone="critical" title="Settings unavailable">{layoutError || "The Social Autopilot backend did not return settings."}</Banner>;
  }
  return (
    <SettingsSections
      settings={settings}
      providers={providers}
      fallbackModels={fallbackModels}
      collections={collections}
      result={result ? { ...result, data: result.data ?? { section: result.ok ? "" : formSection(result.intent) } } : null}
    />
  );
}

function formSection(intent: string): string {
  return intent.startsWith("save:") ? intent.slice(5) : "";
}
