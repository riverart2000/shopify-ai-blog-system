import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData } from "react-router";

import { taskTypeLabel, LoadError } from "../components/agent/parts";
import { ActionBanner, Badge, Card, EmptyState, Field, Hidden, useBusy } from "../components/social/ui";
import {
  TaskAgentError, errorMessage, formString, runAgentIntent, taFetch,
  type AgentActionResult, type JobActionResponse, type PreviewResponse, type Template, type TemplatesResponse,
} from "../lib/task-agent.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "New task" };

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const selectedKey = new URL(request.url).searchParams.get("template") || "";
  try {
    const data = await taFetch<TemplatesResponse>("/templates");
    const templates = data.templates.filter((template) => template.enabled);
    return { templates, taskTypes: data.task_types, selectedKey, error: "" };
  } catch (error) {
    return { templates: [] as Template[], taskTypes: [] as TemplatesResponse["task_types"], selectedKey, error: errorMessage(error) };
  }
};

type NewTaskResult = AgentActionResult & { preview?: PreviewResponse };

function fieldValues(form: FormData): Record<string, string> {
  const values: Record<string, string> = {};
  for (const [key, value] of form.entries()) {
    if (key.startsWith("field:") && typeof value === "string") values[key.slice(6)] = value.trim();
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
      const created = await taFetch<JobActionResponse>("/jobs", {
        body: { template_key: templateKey, values, ...(title ? { title } : {}), priority },
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

function TaskForm({ template, result }: { template: Template; result?: NewTaskResult }) {
  const { busy, isSubmitting } = useBusy();
  const preview = result?.intent === "preview" ? result.preview : undefined;
  const missing = new Set(preview?.missing || []);

  return (
    <Form method="post" className="sa-stack" key={template.key}>
      <Hidden values={{ template_key: template.key }} />
      {template.fields.length ? (
        <div className="sa-grid-2">
          {template.fields.map((field) => (
            <Field
              key={field.name}
              label={<>{field.label}{field.required ? <span className="sa-error-text"> *</span> : <span className="sa-muted"> (optional)</span>}</>}
              error={missing.has(field.name) ? "Please fill this in." : undefined}
            >
              <input
                type="text"
                name={`field:${field.name}`}
                defaultValue={field.default ?? ""}
                required={field.required}
                aria-required={field.required}
              />
            </Field>
          ))}
        </div>
      ) : (
        <p className="sa-muted">This template has no blanks to fill in — it&apos;s ready to queue.</p>
      )}

      <details>
        <summary className="sa-small">More options</summary>
        <div className="sa-grid-2" style={{ marginTop: 8 }}>
          <Field label="Task name (optional)" help="Shown in the task list. Leave blank to name it from the details above.">
            <input type="text" name="title" maxLength={120} />
          </Field>
          <Field label="Priority" help="Higher numbers run first. 0 is normal.">
            <input type="number" name="priority" defaultValue={0} min={-10} max={10} step={1} />
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

export default function AgentNewTask() {
  const { templates, selectedKey, error } = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>() as NewTaskResult | undefined;
  const selected = templates.find((template) => template.key === selectedKey) || null;

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
      <s-section heading="1. Pick a template">
        <p className="sa-muted" style={{ marginBottom: 10 }}>
          Each template is a ready-made task. You fill in the blanks; the agent does the legwork and reports back with its sources.{" "}
          <Link to="/app/agent/templates">Manage templates</Link>
        </p>
        <TemplatePicker templates={templates} selectedKey={selected?.key || ""} />
      </s-section>
      {selected ? (
        <s-section heading={`2. Fill in the details — ${selected.name}`}>
          <TaskForm template={selected} result={result} />
        </s-section>
      ) : (
        <s-section>
          <p className="sa-muted">Choose a template above to fill in the details.</p>
        </s-section>
      )}
    </>
  );
}
