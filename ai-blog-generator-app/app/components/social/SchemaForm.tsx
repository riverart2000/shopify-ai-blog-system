/**
 * Generic form for a pydantic `model_json_schema()` — campaign rule configs.
 *
 * New backend rules render automatically. Handles string / integer / number / boolean,
 * enums (Literal) → select, Optional[...] → "store default", lists (comma or one per line,
 * enum lists → checkboxes, object lists → repeatable sub-forms), dict[str, number] → key/number
 * rows, `$defs`/`$ref` objects, `format: date` → date input. Anything else falls back to a
 * validated JSON textarea. A few field names get purpose-built widgets (collections, products,
 * content types, templates, posts per day, weekday).
 */
import { useState, type ReactNode } from "react";

import type { Collection, JsonSchema, Template } from "../../lib/social-engine.types";
import { ProductPicker, type PickedProduct } from "./ProductPicker";
import { Field } from "./ui";
import { PRODUCT_ORDERS, WEEKDAY_NAMES, contentTypeLabel, humanize } from "./vocab";

export type SchemaContext = {
  contentTypes: Record<string, string>;
  collections: Collection[];
  templates: Template[];
  products: Record<string, PickedProduct>;
  ruleType?: string;
  /** The rule's `allowed_content_types` from the catalogue (null/undefined = any content type). */
  allowedContentTypes?: string[] | null;
};

/** Content-type options for this rule: the catalogue's allowed list, labelled; every type when the rule allows any. */
function contentTypeOptions(context: SchemaContext): Array<[string, string]> {
  const allowed = context.allowedContentTypes;
  if (!allowed?.length) return Object.entries(context.contentTypes);
  return allowed.map((key) => [key, contentTypeLabel(key, context.contentTypes)]);
}

type Value = Record<string, unknown>;

const LABELS: Record<string, string> = {
  order: "Which products go first",
  posts_per_day: "Posts per day",
  template_keys: "Templates to use",
  max_products_per_post: "Max products in a multi-product post",
  allow_reposting: "Allow reposting",
  strategy_hint: "Strategy hint for Gemma",
  theme: "Promotion theme",
  occasion_key: "Occasion",
  scope: "Which products",
  coupon_code: "Coupon code",
  discount_text: "Discount text",
  promo_texts: "Promo lines",
  start_date: "Start date",
  duration_days: "Runs for (days)",
  content_types: "Content types",
  countdown_days: "Countdown posts at the end (days)",
  landing_url: "Landing page URL",
  time: "Time of day",
  weekday: "Day of the week",
  products_per_post: "Products per post",
  content_type: "Content type",
  template_key: "Template",
  collection_ids: "Collections",
  product_ids: "Products",
  occasions: "Occasions",
  custom_occasions: "Custom occasions",
  days_before: "Post this many days before",
  feature_products: "Feature a product in the post",
  lookahead_days: "Look ahead (days)",
  kinds: "Kinds of posts",
  every_n_days: "Post every N days",
  topic_hint: "Topic hint",
  posts_per_week: "Posts per week",
  blog_handles: "Only these blogs",
  min_age_days: "Only re-share posts older than (days)",
  lookback_days: "Look back (days)",
  steps: "Launch series",
  day_offset: "Days after launch",
  delay_minutes: "Wait before posting (minutes)",
  min_drop_pct: "Minimum price drop (%)",
  include_sale_started: "Also post when a sale starts",
  within_minutes: "Post within (minutes)",
  cooldown_days: "Cooldown per product (days)",
};

const HELP: Record<string, string> = {
  allow_reposting: "Off: never repeat a product. On: repeat only after every eligible product has posted once.",
  strategy_hint: "Optional guidance Gemma uses when choosing products and angles, e.g. \"push gift sets in December\".",
  occasion_key: "Optional, e.g. black_friday or christmas — used on holiday templates.",
  promo_texts: "One per line. Leave empty and Gemma writes five for you.",
  days_before: "Comma separated, e.g. 3, 0 (0 = on the day).",
  occasions: "Comma separated occasion keys.",
  blog_handles: "Comma separated blog handles. Empty = all blogs.",
  custom_occasions: "JSON list, e.g. [{\"key\":\"launch_day\",\"name\":\"Launch day\",\"date\":\"2026-11-20\"}]",
  template_keys: "Leave all unticked to let Gemma pick any template for that content type.",
  posts_per_day: "How many posts of each kind per day, on every selected channel (0–3).",
};

const MULTILINE_LISTS = new Set(["promo_texts"]);

function resolve(schema: JsonSchema | undefined, root: JsonSchema): JsonSchema {
  if (!schema) return {};
  if (schema.$ref) {
    const name = schema.$ref.split("/").pop() || "";
    const target = root.$defs?.[name] || {};
    const { $ref: _ignored, ...rest } = schema;
    void _ignored;
    return resolve({ ...target, ...rest }, root);
  }
  if (schema.allOf && schema.allOf.length === 1) {
    const { allOf, ...rest } = schema;
    return resolve({ ...allOf[0], ...rest }, root);
  }
  return schema;
}

/** Optional[X] → {schema: X, nullable: true}. */
function unwrapNullable(schema: JsonSchema, root: JsonSchema): { schema: JsonSchema; nullable: boolean } {
  const options = schema.anyOf || schema.oneOf;
  if (options) {
    const nonNull = options.filter((option) => option.type !== "null");
    if (nonNull.length === 1) {
      const { anyOf: _a, oneOf: _o, ...rest } = schema;
      void _a; void _o;
      return { schema: resolve({ ...resolve(nonNull[0], root), ...rest }, root), nullable: nonNull.length < options.length };
    }
  }
  if (Array.isArray(schema.type) && schema.type.includes("null")) {
    return { schema: { ...schema, type: schema.type.find((type) => type !== "null") }, nullable: true };
  }
  return { schema, nullable: false };
}

function typeOf(schema: JsonSchema): string {
  return Array.isArray(schema.type) ? schema.type[0] : schema.type || (schema.properties ? "object" : schema.enum ? "string" : "");
}

function setKey(value: Value, key: string, next: unknown): Value {
  const copy = { ...value };
  if (next === undefined) delete copy[key];
  else copy[key] = next;
  return copy;
}

export function defaultsFor(schema: JsonSchema, root: JsonSchema = schema): Value {
  const resolved = resolve(schema, root);
  const out: Value = {};
  for (const [key, prop] of Object.entries(resolved.properties || {})) {
    if (prop.default !== undefined) out[key] = prop.default;
  }
  return out;
}

function enumLabel(name: string, option: unknown, context: SchemaContext): string {
  const text = String(option);
  if (name === "order" && PRODUCT_ORDERS[text]) return PRODUCT_ORDERS[text];
  if (name === "scope") return ({ store: "Whole store", collections: "Selected collections", products: "Selected products" } as Record<string, string>)[text] || humanize(text);
  if (name.includes("content_type") && context.contentTypes[text]) return context.contentTypes[text];
  return humanize(text);
}

export function SchemaForm({ schema, value, onChange, context, omit = [] }: {
  schema: JsonSchema; value: Value; onChange: (next: Value) => void; context: SchemaContext; omit?: string[];
}) {
  return <ObjectFields schema={schema} root={schema} value={value} onChange={onChange} context={context} omit={omit} />;
}

function ObjectFields({ schema, root, value, onChange, context, omit = [] }: {
  schema: JsonSchema; root: JsonSchema; value: Value; onChange: (next: Value) => void; context: SchemaContext; omit?: string[];
}) {
  const resolved = resolve(schema, root);
  const required = new Set(resolved.required || []);
  const entries = Object.entries(resolved.properties || {}).filter(([key]) => !omit.includes(key));
  if (!entries.length) return <p className="sa-muted sa-small">This campaign type has no extra settings.</p>;
  return (
    <div className="sa-grid-2">
      {entries.map(([key, prop]) => (
        <SchemaField
          key={key} name={key} schema={prop} root={root} value={value[key]} siblings={value}
          required={required.has(key)} context={context}
          onChange={(next) => onChange(setKey(value, key, next))}
        />
      ))}
    </div>
  );
}

function SchemaField({ name, schema: raw, root, value, siblings, required, context, onChange }: {
  name: string; schema: JsonSchema; root: JsonSchema; value: unknown; siblings: Value; required: boolean;
  context: SchemaContext; onChange: (next: unknown) => void;
}) {
  const { schema, nullable } = unwrapNullable(resolve(raw, root), root);
  const type = typeOf(schema);
  const label = `${LABELS[name] || (schema.title && schema.title !== humanize(name) ? schema.title : humanize(name))}${required ? " *" : ""}`;
  const help = [HELP[name], schema.description].filter(Boolean).join(" ") || undefined;
  const wide = (node: ReactNode) => <div style={{ gridColumn: "1 / -1" }}>{node}</div>;

  // ---- purpose-built widgets -------------------------------------------------
  if (name === "posts_per_day" && type === "object") {
    return wide(<PostsPerDay schema={schema} root={root} value={(value as Record<string, number>) || {}} onChange={onChange} context={context} label={label} help={help} />);
  }
  if (name === "template_keys" && type === "object") {
    return wide(<TemplateKeys value={(value as Record<string, string[]>) || {}} siblings={siblings} onChange={onChange} context={context} label={label} help={help} />);
  }
  if (name === "collection_ids" && type === "array") {
    if (typeof siblings.scope === "string" && siblings.scope !== "collections") return null;
    return wide(
      <fieldset className="sa-fieldset">
        <legend>{label}</legend>
        <CollectionPicker value={(value as string[]) || []} onChange={(next) => onChange(next)} collections={context.collections} />
        {help ? <span className="sa-help">{help}</span> : null}
      </fieldset>,
    );
  }
  if ((name === "product_ids" || name === "exclude_product_ids") && type === "array") {
    if (name === "product_ids" && typeof siblings.scope === "string" && siblings.scope !== "products") return null;
    const ids = ((value as string[]) || []).map(String);
    return wide(
      <fieldset className="sa-fieldset">
        <legend>{label}</legend>
        <ProductPicker
          selected={ids.map((id) => context.products[id] || { product_id: id, title: "", image: "" })}
          onChange={(next) => {
            for (const item of next) context.products[item.product_id] = item;
            onChange(next.map((item) => item.product_id));
          }}
        />
      </fieldset>,
    );
  }
  if (name === "content_type" && type === "string" && !schema.enum) {
    return (
      <Field label={label} help={help}>
        <select value={String(value ?? schema.default ?? "")} onChange={(event) => onChange(event.target.value || undefined)}>
          {contentTypeOptions(context).map(([key, text]) => <option key={key} value={key}>{text}</option>)}
        </select>
      </Field>
    );
  }
  if (name === "content_types" && type === "array" && !schema.items?.enum) {
    const current = new Set(((value as string[]) || (schema.default as string[]) || []).map(String));
    return wide(
      <fieldset className="sa-fieldset">
        <legend>{label}</legend>
        <div className="sa-grid-sm">
          {contentTypeOptions(context).map(([key, text]) => (
            <label key={key} className="sa-check">
              <input type="checkbox" checked={current.has(key)} onChange={(event) => {
                const next = new Set(current);
                if (event.target.checked) next.add(key); else next.delete(key);
                onChange(Array.from(next));
              }} />
              <span>{text}</span>
            </label>
          ))}
        </div>
        {help ? <span className="sa-help">{help}</span> : null}
      </fieldset>,
    );
  }
  if (name === "template_key" && type === "string") {
    const contentType = String(siblings.content_type || "");
    const options = context.templates.filter((template) => !contentType || template.content_type === contentType);
    return (
      <Field label={label} help={help}>
        <select value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>
          <option value="">Automatic (best match)</option>
          {options.map((template) => (
            <option key={template.key} value={template.key}>
              {template.name}{contentType ? "" : ` — ${contentTypeLabel(template.content_type, context.contentTypes)}`}{template.is_active ? "" : " (inactive)"}
            </option>
          ))}
          {value && !options.some((template) => template.key === value) ? <option value={String(value)}>{String(value)}</option> : null}
        </select>
      </Field>
    );
  }
  if (name === "weekday" && type === "integer") {
    return (
      <Field label={label} help={help}>
        <select value={String(value ?? schema.default ?? 0)} onChange={(event) => onChange(Number(event.target.value))}>
          {WEEKDAY_NAMES.map((day, index) => <option key={day} value={index}>{day}</option>)}
        </select>
      </Field>
    );
  }

  // ---- generic -----------------------------------------------------------------
  if (schema.enum) {
    return (
      <Field label={label} help={help}>
        <select
          value={value === undefined || value === null ? (nullable ? "" : String(schema.default ?? "")) : String(value)}
          onChange={(event) => {
            const raw = event.target.value;
            if (raw === "" && nullable) return onChange(null);
            const match = schema.enum?.find((option) => String(option) === raw);
            onChange(match === undefined ? raw : match);
          }}
        >
          {nullable ? <option value="">Store default</option> : null}
          {schema.enum.map((option) => <option key={String(option)} value={String(option)}>{enumLabel(name, option, context)}</option>)}
        </select>
      </Field>
    );
  }

  if (type === "boolean") {
    if (nullable) {
      return (
        <Field label={label} help={help}>
          <select value={value === true ? "true" : value === false ? "false" : ""} onChange={(event) => onChange(event.target.value === "" ? null : event.target.value === "true")}>
            <option value="">Store default</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </Field>
      );
    }
    return (
      <label className="sa-check" style={{ alignSelf: "end" }}>
        <input type="checkbox" checked={Boolean(value ?? schema.default)} onChange={(event) => onChange(event.target.checked)} />
        <span>{label}{help ? <span className="sa-help" style={{ display: "block" }}>{help}</span> : null}</span>
      </label>
    );
  }

  if (type === "integer" || type === "number") {
    const min = schema.minimum ?? (schema.exclusiveMinimum !== undefined ? schema.exclusiveMinimum + (type === "integer" ? 1 : 0) : undefined);
    const max = schema.maximum ?? (schema.exclusiveMaximum !== undefined ? schema.exclusiveMaximum - (type === "integer" ? 1 : 0) : undefined);
    const range = min !== undefined || max !== undefined ? `Between ${min ?? "…"} and ${max ?? "…"}.` : "";
    return (
      <Field label={label} help={[help, range].filter(Boolean).join(" ") || undefined}>
        <input
          type="number" min={min} max={max} step={type === "integer" ? 1 : "any"}
          value={value === undefined || value === null ? "" : String(value)}
          placeholder={schema.default !== undefined && schema.default !== null ? `Default: ${schema.default}` : nullable ? "Store default" : ""}
          onChange={(event) => {
            const raw = event.target.value;
            if (raw === "") return onChange(nullable ? null : undefined);
            onChange(type === "integer" ? Math.trunc(Number(raw)) : Number(raw));
          }}
        />
      </Field>
    );
  }

  if (type === "string") {
    const text = value === undefined || value === null ? "" : String(value);
    const update = (raw: string) => onChange(raw === "" ? (nullable ? null : required ? "" : undefined) : raw);
    if (schema.format === "date") {
      return <Field label={label} help={help}><input type="date" value={text} required={required} onChange={(event) => update(event.target.value)} /></Field>;
    }
    if (name === "time" || schema.pattern === "^([01]\\d|2[0-3]):[0-5]\\d$") {
      return <Field label={label} help={help}><input type="time" value={text || String(schema.default ?? "")} onChange={(event) => update(event.target.value)} /></Field>;
    }
    const long = (schema.maxLength || 0) > 120;
    return (
      <Field label={label} help={[help, schema.maxLength ? `Up to ${schema.maxLength} characters.` : ""].filter(Boolean).join(" ") || undefined}>
        {long ? (
          <textarea rows={2} maxLength={schema.maxLength} value={text} placeholder={schema.default ? String(schema.default) : ""} onChange={(event) => update(event.target.value)} />
        ) : (
          <input type="text" maxLength={schema.maxLength} value={text} placeholder={schema.default ? String(schema.default) : ""} required={required} onChange={(event) => update(event.target.value)} />
        )}
      </Field>
    );
  }

  if (type === "array") {
    const items = unwrapNullable(resolve(schema.items, root), root).schema;
    const itemType = typeOf(items);
    const list = Array.isArray(value) ? value : Array.isArray(schema.default) ? schema.default : [];
    if (items.enum) {
      const current = new Set(list.map(String));
      return wide(
        <fieldset className="sa-fieldset">
          <legend>{label}</legend>
          <div className="sa-row">
            {items.enum.map((option) => (
              <label key={String(option)} className="sa-check">
                <input type="checkbox" checked={current.has(String(option))} onChange={(event) => {
                  const next = items.enum?.filter((candidate) => (String(candidate) === String(option) ? event.target.checked : current.has(String(candidate)))) || [];
                  onChange(next);
                }} />
                <span>{enumLabel(name, option, context)}</span>
              </label>
            ))}
          </div>
          {help ? <span className="sa-help">{help}</span> : null}
        </fieldset>,
      );
    }
    if (itemType === "object" && items.properties) {
      return wide(<ObjectList label={label} help={help} items={items} root={root} value={list as Value[]} onChange={onChange} context={context} />);
    }
    if (itemType === "string" || itemType === "integer" || itemType === "number") {
      return (
        <ListInput
          label={label} help={help} multiline={MULTILINE_LISTS.has(name)} numeric={itemType !== "string"}
          value={list as Array<string | number>} onChange={onChange}
        />
      );
    }
    return wide(<JsonInput label={label} help={help} value={value ?? schema.default ?? []} onChange={onChange} />);
  }

  if (type === "object") {
    const extra = schema.additionalProperties;
    if (schema.properties) {
      return wide(
        <fieldset className="sa-fieldset">
          <legend>{label}</legend>
          <ObjectFields schema={schema} root={root} value={(value as Value) || {}} onChange={onChange} context={context} />
        </fieldset>,
      );
    }
    if (extra && typeof extra === "object" && ["integer", "number", "string"].includes(typeOf(extra))) {
      return wide(<KeyValueRows label={label} help={help} numeric={typeOf(extra) !== "string"} value={(value as Record<string, string | number>) || (schema.default as Record<string, string | number>) || {}} onChange={onChange} />);
    }
  }

  return wide(<JsonInput label={label} help={help} value={value ?? schema.default ?? null} onChange={onChange} />);
}

// ---------------------------------------------------------------------------- widgets

function PostsPerDay({ schema, root, value, onChange, context, label, help }: {
  schema: JsonSchema; root: JsonSchema; value: Record<string, number>; onChange: (next: unknown) => void;
  context: SchemaContext; label: string; help?: string;
}) {
  const names = resolve(schema.propertyNames, root).enum?.map(String)
    || contentTypeOptions(context).map(([key]) => key);
  const keys = Array.from(new Set([...names, ...Object.keys(value)]));
  return (
    <fieldset className="sa-fieldset">
      <legend>{label}</legend>
      <div className="sa-grid">
        {keys.map((key) => (
          <label key={key} className="sa-row" style={{ justifyContent: "space-between", flexWrap: "nowrap" }}>
            <span>{contentTypeLabel(key, context.contentTypes)}</span>
            <select
              value={String(value[key] || 0)} style={{ width: 72 }}
              onChange={(event) => {
                const next = { ...value };
                const count = Number(event.target.value);
                if (count > 0) next[key] = count; else delete next[key];
                onChange(next);
              }}
            >
              {[0, 1, 2, 3].map((count) => <option key={count} value={count}>{count}</option>)}
            </select>
          </label>
        ))}
      </div>
      {help ? <span className="sa-help">{help}</span> : null}
    </fieldset>
  );
}

function TemplateKeys({ value, siblings, onChange, context, label, help }: {
  value: Record<string, string[]>; siblings: Value; onChange: (next: unknown) => void; context: SchemaContext; label: string; help?: string;
}) {
  const perDay = (siblings.posts_per_day as Record<string, number>) || {};
  const types = Array.from(new Set([...Object.keys(perDay).filter((key) => perDay[key] > 0), ...Object.keys(value)]));
  if (!types.length) return null;
  return (
    <fieldset className="sa-fieldset">
      <legend>{label}</legend>
      <div className="sa-stack-sm">
        {types.map((type) => {
          const options = context.templates.filter((template) => template.content_type === type);
          const current = new Set(value[type] || []);
          return (
            <div key={type} className="sa-stack-sm">
              <strong className="sa-small">{contentTypeLabel(type, context.contentTypes)}</strong>
              {options.length ? (
                <div className="sa-row">
                  {options.map((template) => (
                    <label key={template.key} className="sa-check sa-small">
                      <input type="checkbox" checked={current.has(template.key)} onChange={(event) => {
                        const next = new Set(current);
                        if (event.target.checked) next.add(template.key); else next.delete(template.key);
                        const copy = { ...value };
                        if (next.size) copy[type] = Array.from(next); else delete copy[type];
                        onChange(copy);
                      }} />
                      <span>{template.name}</span>
                    </label>
                  ))}
                </div>
              ) : <span className="sa-small sa-muted">No templates for this content type.</span>}
            </div>
          );
        })}
      </div>
      {help ? <span className="sa-help">{help}</span> : null}
    </fieldset>
  );
}

export function CollectionPicker({ value, onChange, collections }: { value: string[]; onChange: (next: string[]) => void; collections: Collection[] }) {
  const current = new Set(value.map(String));
  if (!collections.length) return <span className="sa-small sa-muted">No collections synced yet — sync your catalogue on the Products page.</span>;
  return (
    <div className="sa-grid-sm" style={{ maxHeight: 220, overflowY: "auto" }}>
      {collections.map((collection) => (
        <label key={collection.id} className="sa-check sa-small">
          <input type="checkbox" checked={current.has(String(collection.id))} onChange={(event) => {
            const next = new Set(current);
            if (event.target.checked) next.add(String(collection.id)); else next.delete(String(collection.id));
            onChange(Array.from(next));
          }} />
          <span>{collection.title} <span className="sa-muted">({collection.products_count})</span></span>
        </label>
      ))}
    </div>
  );
}

function ObjectList({ label, help, items, root, value, onChange, context }: {
  label: string; help?: string; items: JsonSchema; root: JsonSchema; value: Value[]; onChange: (next: unknown) => void; context: SchemaContext;
}) {
  return (
    <fieldset className="sa-fieldset">
      <legend>{label}</legend>
      <div className="sa-stack-sm">
        {value.map((item, index) => (
          <div key={index} className="sa-card sa-card-muted sa-stack-sm">
            <div className="sa-spread">
              <strong className="sa-small">Step {index + 1}</strong>
              <button type="button" className="sa-btn sa-btn-sm sa-btn-critical" onClick={() => onChange(value.filter((_, other) => other !== index))}>Remove</button>
            </div>
            <ObjectFields
              schema={items} root={root} value={item || {}} context={context}
              onChange={(next) => onChange(value.map((existing, other) => (other === index ? next : existing)))}
            />
          </div>
        ))}
        <div><button type="button" className="sa-btn sa-btn-sm" onClick={() => onChange([...value, defaultsFor(items, root)])}>Add step</button></div>
      </div>
      {help ? <span className="sa-help">{help}</span> : null}
    </fieldset>
  );
}

function ListInput({ label, help, value, onChange, multiline, numeric }: {
  label: string; help?: string; value: Array<string | number>; onChange: (next: unknown) => void; multiline: boolean; numeric: boolean;
}) {
  const joined = value.join(multiline ? "\n" : ", ");
  // Local text so separators can be typed; the parsed list is what the parent stores.
  const [text, setText] = useState(joined);
  const [error, setError] = useState("");
  const commit = (raw: string) => {
    setText(raw);
    const parts = raw.split(multiline ? /\n/ : /[,\n]/).map((part) => part.trim()).filter(Boolean);
    if (numeric) {
      const numbers = parts.map(Number);
      if (numbers.some((number) => Number.isNaN(number))) {
        setError("Use whole numbers separated by commas.");
        return;
      }
      setError("");
      onChange(numbers);
      return;
    }
    setError("");
    onChange(parts);
  };
  return (
    <Field label={label} help={help || (multiline ? "One per line." : "Separate with commas.")} error={error}>
      {multiline
        ? <textarea rows={4} value={text} onChange={(event) => commit(event.target.value)} />
        : <input type="text" value={text} onChange={(event) => commit(event.target.value)} />}
    </Field>
  );
}

export function KeyValueRows({ label, help, value, onChange, numeric, keyLabel = "Key", keyOptions }: {
  label: string; help?: string; value: Record<string, string | number>; onChange: (next: Record<string, string | number>) => void;
  numeric: boolean; keyLabel?: string; keyOptions?: Array<[string, string]>;
}) {
  const rows = Object.entries(value);
  const [newKey, setNewKey] = useState("");
  return (
    <fieldset className="sa-fieldset">
      <legend>{label}</legend>
      <div className="sa-stack-sm">
        {rows.map(([key, entry]) => (
          <div key={key} className="sa-row" style={{ flexWrap: "nowrap" }}>
            <span className="sa-grow sa-ellipsis">{keyOptions?.find(([option]) => option === key)?.[1] || key}</span>
            <input
              type={numeric ? "number" : "text"} value={String(entry)} aria-label={`${label}: ${key}`} style={{ width: numeric ? 100 : 220 }}
              onChange={(event) => onChange({ ...value, [key]: numeric ? Number(event.target.value || 0) : event.target.value })}
            />
            <button type="button" className="sa-btn sa-btn-sm sa-btn-plain" aria-label={`Remove ${key}`} onClick={() => {
              const copy = { ...value };
              delete copy[key];
              onChange(copy);
            }}>✕</button>
          </div>
        ))}
        <div className="sa-row">
          {keyOptions ? (
            <select value={newKey} onChange={(event) => setNewKey(event.target.value)} aria-label={`New ${keyLabel}`} style={{ width: "auto" }}>
              <option value="">Add {keyLabel.toLowerCase()}…</option>
              {keyOptions.filter(([option]) => !(option in value)).map(([option, text]) => <option key={option} value={option}>{text}</option>)}
            </select>
          ) : (
            <input type="text" value={newKey} onChange={(event) => setNewKey(event.target.value)} placeholder={keyLabel} aria-label={`New ${keyLabel}`} style={{ width: 180 }} />
          )}
          <button type="button" className="sa-btn sa-btn-sm" disabled={!newKey.trim() || newKey.trim() in value} onClick={() => {
            onChange({ ...value, [newKey.trim()]: numeric ? 0 : "" });
            setNewKey("");
          }}>Add</button>
        </div>
      </div>
      {help ? <span className="sa-help">{help}</span> : null}
    </fieldset>
  );
}

export function JsonInput({ label, help, value, onChange }: { label: string; help?: string; value: unknown; onChange: (next: unknown) => void }) {
  const serialized = JSON.stringify(value ?? null, null, 2);
  const [text, setText] = useState(serialized);
  const [error, setError] = useState("");
  return (
    <Field label={label} help={help || "Advanced setting — edit as JSON."} error={error}>
      <textarea
        rows={4} value={text} spellCheck={false} style={{ fontFamily: "ui-monospace, Menlo, monospace", fontSize: "0.75rem" }}
        onChange={(event) => {
          setText(event.target.value);
          try {
            const parsed = JSON.parse(event.target.value || "null");
            setError("");
            onChange(parsed);
          } catch (parseError) {
            setError(`Not valid JSON yet: ${parseError instanceof Error ? parseError.message : "parse error"}`);
          }
        }}
      />
    </Field>
  );
}
