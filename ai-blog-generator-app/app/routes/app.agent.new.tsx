import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData } from "react-router";

import { taskTypeLabel, LoadError } from "../components/agent/parts";
import { ActionBanner, Badge, Banner, Card, EmptyState, Field, Hidden, useBusy } from "../components/social/ui";
import {
  TaskAgentError, errorMessage, formString, runAgentIntent, taFetch,
  type AgentActionResult, type JobActionResponse, type JobResponse, type PreviewResponse, type Template,
  type TemplateField, type TemplatesResponse,
} from "../lib/task-agent.server";
import { seg } from "../lib/task-agent.shared";
import { authenticate } from "../shopify.server";

export const handle = { title: "New task" };

/** The task being cloned (`?clone=<jobId>`): its template, title and the values it was created with. */
type CloneInfo = {
  id: number;
  templateKey: string;
  title: string;
  priority: number;
  /** Null when the task is older than cloning, so its values weren't kept. */
  values: Record<string, string> | null;
  error: string;
};

/** A saved template value → the text to put back in its box (lists go one per line). */
function cloneValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map((item) => (typeof item === "string" ? item : JSON.stringify(item))).join("\n");
  return JSON.stringify(value);
}

async function loadClone(raw: string): Promise<CloneInfo> {
  const id = /^\d+$/.test(raw) ? Number(raw) : 0;
  const empty: CloneInfo = { id, templateKey: "", title: "", priority: 0, values: null, error: "" };
  if (!id) return { ...empty, error: `“${raw}” isn't a task number.` };
  try {
    const { job } = await taFetch<JobResponse>(`/jobs/${seg(id)}`);
    if (!job.template_key) return { ...empty, error: `Task ${id} wasn't made from a template, so it can't be copied.` };
    const saved = job.template_values && typeof job.template_values === "object" ? job.template_values : null;
    // Only a title Joe typed is copied ("_title"); a made-up one would name the old event.
    const typedTitle = saved && typeof saved._title === "string" ? saved._title : "";
    return {
      ...empty,
      templateKey: job.template_key,
      title: typedTitle,
      priority: Number.isInteger(job.priority) ? job.priority : 0,
      values: saved ? Object.fromEntries(Object.entries(saved).map(([name, value]) => [name, cloneValue(value)])) : null,
    };
  } catch (error) {
    return { ...empty, error: errorMessage(error) };
  }
}

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const params = new URL(request.url).searchParams;
  const cloneRaw = (params.get("clone") || "").trim();
  const [templatesResult, clone] = await Promise.all([
    taFetch<TemplatesResponse>("/templates").then(
      (data) => ({ data, error: "" }),
      (error: unknown) => ({ data: null, error: errorMessage(error) }),
    ),
    cloneRaw ? loadClone(cloneRaw) : Promise.resolve(null),
  ]);
  // Cloning picks the old task's template, whatever ?template= says.
  const selectedKey = clone?.templateKey || params.get("template") || "";
  if (!templatesResult.data) {
    return { templates: [] as Template[], taskTypes: [] as TemplatesResponse["task_types"], selectedKey, clone, error: templatesResult.error };
  }
  const templates = templatesResult.data.templates.filter((template) => template.enabled);
  return { templates, taskTypes: templatesResult.data.task_types, selectedKey, clone, error: "" };
};

type NewTaskResult = AgentActionResult & { preview?: PreviewResponse };

function fieldValues(form: FormData): Record<string, string> {
  const values: Record<string, string> = {};
  for (const [key, value] of form.entries()) {
    // Textareas submit line breaks as \r\n; keep them, as plain \n.
    if (key.startsWith("field:") && typeof value === "string") values[key.slice(6)] = value.replace(/\r\n?/g, "\n").trim();
  }
  return values;
}

export const action = async ({ request }: ActionFunctionArgs): Promise<NewTaskResult | Response> => {
  const { redirect } = await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");
  const templateKey = formString(form, "template_key");
  const values = fieldValues(form);

  if (intent === "preview") {
    try {
      const preview = await taFetch<PreviewResponse>("/templates/preview", { body: { template_key: templateKey, values } });
      return {
        ok: true,
        intent,
        preview,
        message: preview.missing.length ? "" : "This is exactly what the agent will be asked.",
      };
    } catch (error) {
      return { ok: false, intent, error: errorMessage(error, "The preview didn't work.") };
    }
  }

  if (intent === "queue") {
    let jobId = 0;
    const result = await runAgentIntent(intent, async () => {
      const title = formString(form, "title");
      const priorityRaw = formString(form, "priority") || "0";
      const priority = Number(priorityRaw);
      if (!Number.isInteger(priority)) throw new TaskAgentError(`Priority must be a whole number (got “${priorityRaw}”).`);
      const cloneOf = formString(form, "clone_of");
      if (cloneOf && !/^\d+$/.test(cloneOf)) throw new TaskAgentError(`“${cloneOf}” isn't a task number.`);
      const created = await taFetch<JobActionResponse>("/jobs", {
        body: {
          template_key: templateKey, values, ...(title ? { title } : {}), priority,
          ...(cloneOf ? { clone_of: Number(cloneOf) } : {}),
        },
      });
      jobId = created.job.id;
      return { message: created.message };
    });
    if (result.ok && jobId) return redirect(`/app/agent/tasks/${jobId}`);
    return result;
  }

  return { ok: false, intent, error: `Unknown action “${intent}”.` };
};

function TemplatePicker({ templates, selectedKey }: { templates: Template[]; selectedKey: string }) {
  return (
    <div className="sa-grid">
      {templates.map((template) => {
        const selected = template.key === selectedKey;
        return (
          <Card key={template.key} accent={selected ? "info" : undefined}>
            <div className="sa-stack-sm">
              <div className="sa-row">
                <strong>{template.name}</strong>
                {!template.builtin ? <Badge tone="magic">Yours</Badge> : null}
              </div>
              <span className="sa-small sa-muted">{taskTypeLabel(template.task_type)}</span>
              {template.description ? <p className="sa-small">{template.description}</p> : null}
              <div>
                {selected
                  ? <Badge tone="info">Selected</Badge>
                  : <Link to={`?template=${encodeURIComponent(template.key)}`} className="sa-btn sa-btn-sm" preventScrollReset>Use this</Link>}
              </div>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

/** A blank whose default is "yes" or "no" is a yes/no question: show a choice instead of a text box. */
function isYesNo(field: TemplateField): boolean {
  return /^(yes|no)$/i.test((field.default ?? "").trim());
}

/** The choice to start on: the saved/default value when it's still offered, else the first one (or none when optional). */
function startingOption(field: TemplateField, value: string): string {
  const options = field.options || [];
  const offered = (candidate: string) => options.some((option) => option.value === candidate);
  if (value && offered(value)) return value;
  if (field.default && offered(field.default)) return field.default;
  return field.required ? options[0]?.value ?? "" : "";
}

function FieldInput({ field, value }: { field: TemplateField; value: string }) {
  const name = `field:${field.name}`;
  if (field.options) {
    if (!field.options.length) {
      return (
        <span className="sa-error-text">
          Nothing to choose yet. <Link to="/app/agent/settings#event-types">Add an event type in Settings first</Link>.
        </span>
      );
    }
    return (
      <select name={name} defaultValue={startingOption(field, value)} required={field.required} aria-required={field.required}>
        {!field.required ? <option value="">None</option> : null}
        {field.options.map((option) => <option key={option.value} value={option.value}>{option.label || option.value}</option>)}
      </select>
    );
  }
  if (field.multiline) {
    return <textarea name={name} rows={4} defaultValue={value} required={field.required} aria-required={field.required} />;
  }
  if (isYesNo(field)) {
    return (
      <select name={name} defaultValue={/^n/i.test(value.trim()) ? "no" : "yes"}>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    );
  }
  return <input type="text" name={name} defaultValue={value} required={field.required} aria-required={field.required} />;
}

function TaskForm({ template, result, clone }: { template: Template; result?: NewTaskResult; clone: CloneInfo | null }) {
  const { busy, isSubmitting } = useBusy();
  const preview = result?.intent === "preview" ? result.preview : undefined;
  const missing = new Set(preview?.missing || []);
  const valueFor = (field: TemplateField) => clone?.values?.[field.name] ?? field.default ?? "";

  return (
    <Form method="post" className="sa-stack" key={`${template.key}:${clone?.id || ""}`}>
      <Hidden values={{ template_key: template.key, ...(clone ? { clone_of: clone.id } : {}) }} />
      {template.fields.length ? (
        <div className="sa-grid-2">
          {template.fields.map((field) => {
            const value = valueFor(field);
            // A copied task's choice that's no longer offered (switched off or deleted): say so, don't swap it silently.
            const copied = clone?.values?.[field.name] || "";
            const gone = Boolean(field.options?.length && copied && !field.options.some((option) => option.value === copied));
            const box = (
              <Field
                key={field.name}
                label={<>{field.label}{field.required ? <span className="sa-error-text"> *</span> : <span className="sa-muted"> (optional)</span>}</>}
                help={gone ? `The copied task used “${copied}”, which isn't available any more (switched off or deleted). Pick one.` : undefined}
                error={missing.has(field.name) ? "Please fill this in." : undefined}
              >
                <FieldInput field={field} value={value} />
              </Field>
            );
            // Lists of links, accounts and notes get the full width.
            return field.multiline ? <div key={field.name} style={{ gridColumn: "1 / -1" }}>{box}</div> : box;
          })}
        </div>
      ) : (
        <p className="sa-muted">This template has no blanks to fill in — it&apos;s ready to queue.</p>
      )}

      <details open={Boolean(clone?.title)}>
        <summary className="sa-small">More options</summary>
        <div className="sa-grid-2" style={{ marginTop: 8 }}>
          <Field
            label="Task name (optional)"
            help={clone?.title
              ? `Copied from task ${clone.id}. Change it if it no longer fits, or clear it to name it from the details above.`
              : "Shown in the task list. Leave blank to name it from the details above."}
          >
            <input type="text" name="title" maxLength={120} defaultValue={clone?.title || ""} />
          </Field>
          <Field label="Priority" help="Higher numbers run first. 0 is normal.">
            <input type="number" name="priority" defaultValue={clone?.priority ?? 0} min={-10} max={10} step={1} />
          </Field>
        </div>
      </details>

      {preview ? (
        <Card muted>
          <div className="sa-stack-sm">
            <strong>The agent will be asked:</strong>
            <div className="sa-prewrap">{preview.prompt}</div>
            {preview.missing.length ? (
              <span className="sa-error-text sa-small">Still to fill in: {preview.missing.join(", ")}</span>
            ) : null}
          </div>
        </Card>
      ) : null}

      <div className="sa-form-actions">
        <button type="submit" name="intent" value="queue" className="sa-btn sa-btn-primary" disabled={busy}>
          {isSubmitting("queue") ? "Queuing…" : "Queue task"}
        </button>
        <button type="submit" name="intent" value="preview" className="sa-btn" disabled={busy} formNoValidate>
          {isSubmitting("preview") ? "Building preview…" : "Preview"}
        </button>
        <span className="sa-small sa-muted">Tasks run one at a time, in the background. You can leave this page.</span>
      </div>
    </Form>
  );
}

/** What happened when copying `?clone=` (shown above the form). */
function CloneNote({ clone, selected }: { clone: CloneInfo; selected: Template | null }) {
  const task = <Link to={`/app/agent/tasks/${seg(clone.id)}`}>task {clone.id}</Link>;
  if (clone.error) {
    return <Banner tone="warning" title={clone.id ? `Couldn't copy task ${clone.id}` : "Couldn't copy that task"}>{clone.error}</Banner>;
  }
  if (!selected) {
    return (
      <Banner tone="warning" title={`Couldn't copy task ${clone.id}`}>
        Its template (“{clone.templateKey}”) is switched off or was deleted. Turn it back on in{" "}
        <Link to="/app/agent/templates">Templates</Link>, or pick another template below.
      </Banner>
    );
  }
  if (!clone.values) {
    return (
      <Banner tone="warning">
        Picked the same template as {task}, but its details couldn&apos;t be copied (it&apos;s an older task). Fill them in again below.
      </Banner>
    );
  }
  return <Banner tone="info">Copied from {task}. Change what&apos;s different (e.g. the event links) and queue it.</Banner>;
}

export default function AgentNewTask() {
  const { templates, selectedKey, clone, error } = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>() as NewTaskResult | undefined;
  const selected = templates.find((template) => template.key === selectedKey) || null;
  // Only copy values into the template they came from.
  const cloneForForm = clone && !clone.error && selected && clone.templateKey === selected.key ? clone : null;

  if (!templates.length) {
    return (
      <>
        <LoadError error={error} />
        <s-section>
          <EmptyState title="No templates to start from" action={<Link to="/app/agent/templates" className="sa-btn">Manage templates</Link>}>
            {error ? "The templates couldn't be loaded." : "Every template is switched off. Turn one on, or create your own."}
          </EmptyState>
        </s-section>
      </>
    );
  }

  return (
    <>
      <LoadError error={error} />
      {result && result.intent !== "preview" ? <ActionBanner result={result} /> : null}
      {result?.intent === "preview" && !result.ok ? <ActionBanner result={result} /> : null}
      {clone ? <CloneNote clone={clone} selected={selected} /> : null}
      <s-section heading="1. Pick a template">
        <p className="sa-muted" style={{ marginBottom: 10 }}>
          Each template is a ready-made task. You fill in the blanks; the agent does the legwork and reports back with its sources.{" "}
          <Link to="/app/agent/templates">Manage templates</Link>
        </p>
        <TemplatePicker templates={templates} selectedKey={selected?.key || ""} />
      </s-section>
      {selected ? (
        <s-section heading={`2. Fill in the details — ${selected.name}`}>
          <TaskForm template={selected} result={result} clone={cloneForForm} />
        </s-section>
      ) : (
        <s-section>
          <p className="sa-muted">Choose a template above to fill in the details.</p>
        </s-section>
      )}
    </>
  );
}
