import { useMemo, useState } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, useActionData, useLoaderData } from "react-router";

import { useSocial } from "../components/social/context";
import { ActionBanner, Badge, EmptyState, Field, Hidden, LoadError, Toggle, useBusy } from "../components/social/ui";
import { contentTypeLabel, humanize } from "../components/social/vocab";
import {
  SocialEngineError, errorMessage, formBool, formString, runIntent, seFetch, seg, signedTemplatePreviewUrl,
  type BrandColors, type BrandKit, type Template, type TemplatesResponse,
} from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "Templates & brand kit" };

type TemplateWithPreviews = Template & { previews: Array<{ format: string; url: string }> };

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  try {
    const data = await seFetch<TemplatesResponse>("/templates");
    return {
      templates: data.templates.map((template) => ({
        ...template,
        previews: template.formats.map((format) => ({ format, url: signedTemplatePreviewUrl(template.key, format) })),
      })),
      error: "",
    };
  } catch (error) {
    return { templates: [] as TemplateWithPreviews[], error: errorMessage(error) };
  }
};

const COLOR_KEYS: Array<[keyof BrandColors, string, string]> = [
  ["primary", "Primary", "Main brand colour (backgrounds, shapes)"],
  ["secondary", "Secondary", "Second brand colour"],
  ["accent", "Accent", "Details and highlights"],
  ["cta", "Button", "“Shop now” button"],
  ["text", "Text", "Headlines and body text"],
  ["background", "Background", "Light backgrounds"],
];
const HEX = /^#[0-9a-fA-F]{6}$/;

export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  return runIntent(intent, async () => {
    if (intent === "active") {
      const key = formString(form, "key");
      const active = formBool(form, "active");
      await seFetch(`/templates/${seg(key)}/active`, { body: { active } });
      return { message: active ? "Template switched on — campaigns can use it." : "Template switched off — it won't be used automatically." };
    }
    if (intent === "seed") {
      const result = await seFetch<{ count: number }>("/templates/seed", { method: "POST" });
      return { message: `Built-in templates refreshed (${result.count}).` };
    }
    if (intent === "brand") {
      const colors: Partial<BrandColors> = {};
      for (const [key, label] of COLOR_KEYS) {
        const value = formString(form, `color:${key}`);
        if (!HEX.test(value)) throw new SocialEngineError(`${label} colour must look like #1a2b3c (got “${value}”).`);
        colors[key] = value;
      }
      const opacity = Number(formString(form, "logo_opacity") || 1);
      const taxes = Number(formString(form, "include_taxes_pct") || 0);
      const brand: Partial<BrandKit> = {
        business_name: formString(form, "business_name"),
        description: formString(form, "description"),
        what_you_sell: formString(form, "what_you_sell"),
        website: formString(form, "website"),
        slogan: formString(form, "slogan"),
        cta_text: formString(form, "cta_text") || "Shop Now",
        colors: colors as BrandColors,
        use_store_logo: formBool(form, "use_store_logo"),
        logo_position: formString(form, "logo_position") as BrandKit["logo_position"],
        logo_opacity: opacity,
        show_price: formBool(form, "show_price"),
        currency_symbol: formString(form, "currency_symbol"),
        include_taxes_pct: taxes,
      };
      await seFetch("/settings", { body: { patch: { brand } } });
      return { message: "Brand kit saved. New posts and template previews use it from now on." };
    }
    throw new SocialEngineError(`Unknown action "${intent}".`);
  });
};

function hash(value: unknown): string {
  const text = JSON.stringify(value ?? "");
  let h = 0;
  for (let index = 0; index < text.length; index += 1) h = (h * 31 + text.charCodeAt(index)) | 0;
  return (h >>> 0).toString(36);
}

function ColorField({ name, label, help, initial }: { name: string; label: string; help: string; initial: string }) {
  const [value, setValue] = useState(initial);
  return (
    <Field label={label} help={help} error={HEX.test(value) ? undefined : "Use #RRGGBB"}>
      <span className="sa-row" style={{ flexWrap: "nowrap" }}>
        <input type="color" value={HEX.test(value) ? value : "#000000"} onChange={(event) => setValue(event.target.value)} aria-label={`${label} colour picker`} />
        <input type="text" name={name} value={value} onChange={(event) => setValue(event.target.value.trim())} maxLength={7} style={{ width: 110, fontFamily: "ui-monospace, Menlo, monospace" }} />
      </span>
    </Field>
  );
}

function BrandForm({ brand, hasLogo }: { brand: BrandKit; hasLogo: boolean }) {
  const { busy, isSubmitting } = useBusy();
  return (
    <Form method="post" className="sa-stack" preventScrollReset>
      <Hidden values={{ intent: "brand" }} />
      <div className="sa-grid-2">
        <Field label="Business name"><input type="text" name="business_name" defaultValue={brand.business_name} /></Field>
        <Field label="Website"><input type="url" name="website" defaultValue={brand.website} placeholder="https://…" /></Field>
        <Field label="Slogan"><input type="text" name="slogan" defaultValue={brand.slogan} /></Field>
        <Field label="Button text" help="Shown on images and used as the call to action."><input type="text" name="cta_text" defaultValue={brand.cta_text} maxLength={30} /></Field>
      </div>
      <div className="sa-grid-2">
        <Field label="About your business" help="Gemma uses this to write captions that sound like you."><textarea name="description" rows={3} defaultValue={brand.description} /></Field>
        <Field label="What you sell" help="e.g. “plant-based supplements for sleep and recovery”."><textarea name="what_you_sell" rows={3} defaultValue={brand.what_you_sell} /></Field>
      </div>
      <fieldset className="sa-fieldset">
        <legend>Colours</legend>
        <div className="sa-grid">
          {COLOR_KEYS.map(([key, label, help]) => <ColorField key={key} name={`color:${key}`} label={label} help={help} initial={brand.colors[key]} />)}
        </div>
      </fieldset>
      <fieldset className="sa-fieldset">
        <legend>Logo &amp; price</legend>
        <div className="sa-grid">
          <Toggle name="use_store_logo" checked={brand.use_store_logo} label="Show my store logo" help={hasLogo ? "Uses the logo uploaded in the app's Settings." : "No logo uploaded yet — add one in the app's Settings."} />
          <Field label="Logo position">
            <select name="logo_position" defaultValue={brand.logo_position}>
              {["top_left", "top_right", "bottom_left", "bottom_right", "none"].map((position) => <option key={position} value={position}>{position === "none" ? "No logo" : humanize(position)}</option>)}
            </select>
          </Field>
          <Field label="Logo opacity" help="0.1 (faint) to 1 (solid).">
            <input type="number" name="logo_opacity" min={0.1} max={1} step={0.05} defaultValue={brand.logo_opacity} />
          </Field>
          <Toggle name="show_price" checked={brand.show_price} label="Show prices on images" />
          <Field label="Currency symbol" help="Display only.">
            <input type="text" name="currency_symbol" defaultValue={brand.currency_symbol} maxLength={4} />
          </Field>
          <Field label="Add taxes to shown prices (%)" help="0 if your prices already include tax.">
            <input type="number" name="include_taxes_pct" min={0} max={100} step={0.5} defaultValue={brand.include_taxes_pct} />
          </Field>
        </div>
      </fieldset>
      <div className="sa-form-actions">
        <button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>{isSubmitting("brand") ? "Saving…" : "Save brand kit"}</button>
      </div>
    </Form>
  );
}

export default function TemplatesPage() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>();
  const { settings, contentTypes, hasLogo } = useSocial();
  const { busy, isSubmitting } = useBusy();
  const version = hash(settings?.brand);
  const groups = useMemo(() => {
    const map = new Map<string, TemplateWithPreviews[]>();
    for (const template of data.templates) map.set(template.content_type, [...(map.get(template.content_type) || []), template]);
    return Array.from(map.entries()).sort(([a], [b]) => contentTypeLabel(a, contentTypes).localeCompare(contentTypeLabel(b, contentTypes)));
  }, [data.templates, contentTypes]);

  return (
    <>
      <ActionBanner result={result} />
      <LoadError error={data.error} />

      <s-section heading="Brand kit">
        {settings ? <BrandForm key={version} brand={settings.brand} hasLogo={hasLogo} /> : <p className="sa-muted">Settings are unavailable, so the brand kit can&apos;t be edited right now.</p>}
      </s-section>

      <s-section heading="Templates">
        <div className="sa-spread" style={{ marginBottom: 10 }}>
          <p className="sa-muted">Previews use sample products in your brand colours. Switched-off templates are never chosen automatically.</p>
          <Form method="post"><Hidden values={{ intent: "seed" }} /><button type="submit" className="sa-btn sa-btn-sm" disabled={busy}>{isSubmitting("seed") ? "Refreshing…" : "Restore built-in templates"}</button></Form>
        </div>
        {groups.length ? (
          <div className="sa-stack-lg">
            {groups.map(([type, templates]) => (
              <div key={type} className="sa-stack-sm">
                <h3>{contentTypeLabel(type, contentTypes)} <span className="sa-muted sa-small">({templates.length})</span></h3>
                <div className="sa-grid-lg">
                  {templates.map((template) => (
                    <div key={template.key} className={`sa-card sa-stack-sm ${template.is_active ? "" : "sa-card-muted"}`}>
                      <div className="sa-spread">
                        <div className="sa-row">
                          <strong>{template.name}</strong>
                          <Badge tone={template.kind === "video" ? "magic" : "neutral"}>{humanize(template.kind)}</Badge>
                          {template.is_builtin ? <Badge tone="info">Built-in</Badge> : null}
                        </div>
                        <Form method="post" preventScrollReset>
                          <Hidden values={{ intent: "active", key: template.key, active: template.is_active ? "0" : "1" }} />
                          <button type="submit" className={`sa-btn sa-btn-sm ${template.is_active ? "" : "sa-btn-primary"}`} disabled={busy}>
                            {template.is_active ? "On" : "Off"}
                          </button>
                        </Form>
                      </div>
                      <div className="sa-grid-sm">
                        {template.previews.map((preview) => (
                          <figure key={preview.format} style={{ margin: 0 }} className="sa-stack-sm">
                            {preview.url ? (
                              <img className="sa-media" src={`${preview.url}&v=${version}`} alt={`${template.name} — ${preview.format}`} loading="lazy" />
                            ) : <div className="sa-placeholder" style={{ aspectRatio: "1 / 1" }}>No preview</div>}
                            <figcaption className="sa-small sa-muted">{humanize(preview.format)}{template.kind === "video" ? " · cover frame" : ""}</figcaption>
                          </figure>
                        ))}
                      </div>
                      <span className="sa-small sa-muted"><code>{template.key}</code></span>
                      {template.caption_template ? (
                        <details className="sa-plain"><summary className="sa-small sa-muted">Caption template</summary><div className="sa-prewrap sa-small">{template.caption_template}</div></details>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : !data.error ? (
          <EmptyState title="No templates yet" action={<Form method="post"><Hidden values={{ intent: "seed" }} /><button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>Add the built-in templates</button></Form>}>
            Templates turn product photos into branded social images and videos.
          </EmptyState>
        ) : null}
      </s-section>
    </>
  );
}
