import { useState } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData } from "react-router";

import { taskTypeLabel, LoadError } from "../components/agent/parts";
import { ActionBanner, Badge, Banner, EmptyState, Field, Hidden, Toggle, useBusy } from "../components/social/ui";
import {
  TaskAgentError, errorMessage, formBool, formString, runAgentIntent, taFetch,
  type AgentActionResult, type MessageResponse, type TaskType, type Template, type TemplateSaveResponse, type TemplatesResponse,
} from "../lib/task-agent.server";
import { seg } from "../lib/task-agent.shared";
import { authenticate } from "../shopify.server";

export const handle = { title: "Templates" };

type EditorMode = { kind: "new" } | { kind: "edit"; template: Template } | { kind: "duplicate"; template: Template };

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const url = new URL(request.url);
  const edit = url.searchParams.get("edit") || "";
  const duplicate = url.searchParams.get("duplicate") || "";
  const isNew = url.searchParams.get("new") === "1";
  const saved = url.searchParams.get("saved") || "";
  try {
    const data = await taFetch<TemplatesResponse>("/templates");
    return { templates: data.templates, taskTypes: data.task_types, edit, duplicate, isNew, saved, error: "" };
  } catch (error) {
    return {
      templates: [] as Template[], taskTypes: [] as TaskType[], edit, duplicate, isNew, saved, error: errorMessage(error),
    };
  }
};

export const action = async ({ request }: ActionFunctionArgs): Promise<AgentActionResult | Response> => {
  const { redirect } = await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");

  if (intent === "save") {
    let savedKey = "";
    const result = await runAgentIntent(intent, async () => {
      const taskType = formString(form, "task_type");
      let defaults: Record<string, unknown> = {};
      try {
        defaults = JSON.parse(formString(form, "defaults_json") || "{}") as Record<string, unknown>;
      } catch {
        defaults = {};
      }
      if (taskType === "research") {
        const context = formString(form, "context");
        const minSourcesRaw = formString(form, "min_sources");
        if (context) defaults.context = context;
        else delete defaults.context;
        if (minSourcesRaw) {
          const minSources = Number(minSourcesRaw);
          if (!Number.isInteger(minSources) || minSources < 1 || minSources > 8) {
            throw new TaskAgentError("Minimum sources must be a whole number from 1 to 8.");
          }
          defaults.min_sources = minSources;
        } else {
          delete defaults.min_sources;
        }
      }
      const key = formString(form, "key");
      const saved = await taFetch<TemplateSaveResponse>("/templates", {
        body: {
          ...(key ? { key } : {}),
          name: formString(form, "name"),
          description: formString(form, "description"),
          task_type: taskType,
          prompt: formString(form, "prompt"),
          defaults,
          enabled: formBool(form, "enabled"),
        },
      });
      savedKey = saved.template.key;
      return { message: saved.message };
    });
    if (result.ok && savedKey) return redirect(`/app/agent/templates?saved=${encodeURIComponent(savedKey)}`);
    return result;
  }

  if (intent === "delete") {
    const key = formString(form, "key");
    return runAgentIntent(intent, async () => {
      const result = await taFetch<MessageResponse>(`/templates/${seg(key)}/delete`, { method: "POST" });
      return { message: result.message };
    }, key);
  }

  if (intent === "toggle") {
    // Switch one of your own templates on or off (re-saves it unchanged otherwise).
    const key = formString(form, "key");
    return runAgentIntent(intent, async () => {
      const data = await taFetch<TemplatesResponse>("/templates");
      const template = data.templates.find((item) => item.key === key);
      if (!template) throw new TaskAgentError("That template no longer exists.");
      const enabled = formBool(form, "enabled");
      await taFetch<TemplateSaveResponse>("/templates", {
        body: {
          key, name: template.name, description: template.description, task_type: template.task_type,
          prompt: template.prompt, defaults: template.defaults, enabled,
        },
      });
      return { message: enabled ? `“${template.name}” is switched on.` : `“${template.name}” is switched off — it won't show on New task.` };
    }, key);
  }

  return { ok: false, intent, error: `Unknown action “${intent}”.` };
};

const PLACEHOLDER_HELP = (
  <>
    Write the task in plain English. Put blanks in curly brackets and they become boxes to fill in:{" "}
    <code>{"{event_name}"}</code> must be filled in, <code>{"{notes?}"}</code> is optional, and{" "}
    <code>{"{how_many=5}"}</code> is optional and starts at 5.
  </>
);

function TemplateEditor({ mode, taskTypes }: { mode: EditorMode; taskTypes: TaskType[] }) {
  const { busy, isSubmitting } = useBusy();
  const source = mode.kind === "new" ? null : mode.template;
  const [taskType, setTaskType] = useState(source?.task_type || taskTypes[0]?.type || "research");
  const defaults = { ...(source?.defaults || {}) };
  const context = typeof defaults.context === "string" ? defaults.context : "";
  const minSources = typeof defaults.min_sources === "number" ? String(defaults.min_sources) : "";
  delete defaults.context;
  delete defaults.min_sources;
  const heading = mode.kind === "edit" ? `Edit “${mode.template.name}”` : mode.kind === "duplicate" ? `Copy of “${mode.template.name}”` : "New template";
  const typeInfo = taskTypes.find((item) => item.type === taskType);

  return (
    <s-section heading={heading}>
      <Form method="post" className="sa-stack" key={`${mode.kind}-${source?.key || "new"}`}>
        <Hidden values={{ intent: "save", key: mode.kind === "edit" ? mode.template.key : "", defaults_json: JSON.stringify(defaults) }} />
        <div className="sa-grid-2">
          <Field label="Name">
            <input type="text" name="name" required maxLength={80} defaultValue={mode.kind === "duplicate" ? `${mode.template.name} (copy)` : source?.name || ""} />
          </Field>
          <Field label="Task type" help={typeInfo?.summary}>
            <select name="task_type" value={taskType} onChange={(event) => setTaskType(event.target.value)}>
              {taskTypes.map((item) => <option key={item.type} value={item.type}>{taskTypeLabel(item.type)}</option>)}
            </select>
          </Field>
        </div>
        <Field label="Description" help="One line shown on the template card.">
          <input type="text" name="description" maxLength={200} defaultValue={source?.description || ""} />
        </Field>
        <Field label="What the agent should do" help={PLACEHOLDER_HELP}>
          <textarea name="prompt" required rows={6} defaultValue={source?.prompt || ""} />
        </Field>
        {taskType === "research" ? (
          <div className="sa-grid-2">
            <Field label="Background (optional)" help="Anything the agent should know every time, e.g. who we are and who we sell to.">
              <textarea name="context" rows={4} defaultValue={context} />
            </Field>
            <Field label="Minimum sources (optional)" help="How many web pages it must open and cite (1–8). Blank uses the default of 2.">
              <input type="number" name="min_sources" min={1} max={8} step={1} defaultValue={minSources} />
            </Field>
          </div>
        ) : null}
        <Toggle name="enabled" checked={source ? source.enabled : true} label="Show this template on New task" />
        <div className="sa-form-actions">
          <button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>{isSubmitting("save") ? "Saving…" : "Save template"}</button>
          <Link to="/app/agent/templates" className="sa-btn sa-btn-plain">Cancel</Link>
        </div>
      </Form>
    </s-section>
  );
}

export default function AgentTemplates() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>() as AgentActionResult | undefined;
  const { busy } = useBusy();

  const findTemplate = (key: string) => data.templates.find((template) => template.key === key);
  const editTarget = data.edit ? findTemplate(data.edit) : undefined;
  const duplicateTarget = data.duplicate ? findTemplate(data.duplicate) : undefined;
  let mode: EditorMode | null = null;
  if (editTarget && !editTarget.builtin) mode = { kind: "edit", template: editTarget };
  else if (duplicateTarget) mode = { kind: "duplicate", template: duplicateTarget };
  else if (data.isNew) mode = { kind: "new" };
  const savedTemplate = data.saved ? findTemplate(data.saved) : undefined;

  const builtins = data.templates.filter((template) => template.builtin);
  const own = data.templates.filter((template) => !template.builtin);

  const renderRow = (template: Template) => (
    <tr key={template.key}>
      <td className="sa-break">
        <strong>{template.name}</strong>
        {template.description ? <div className="sa-small sa-muted">{template.description}</div> : null}
      </td>
      <td className="sa-nowrap">{taskTypeLabel(template.task_type)}</td>
      <td className="sa-small">{template.fields.length ? template.fields.map((field) => field.label).join(", ") : <span className="sa-muted">None</span>}</td>
      <td>
        {template.builtin ? (
          template.enabled ? <Badge tone="success">On</Badge> : <Badge tone="neutral">Off</Badge>
        ) : (
          <Form method="post" className="sa-inline-form" preventScrollReset>
            <Hidden values={{ intent: "toggle", key: template.key, enabled: template.enabled ? "0" : "1" }} />
            <button type="submit" className="sa-btn sa-btn-sm" disabled={busy} title={template.enabled ? "Hide it from New task" : "Show it on New task"}>
              {template.enabled ? "On — switch off" : "Off — switch on"}
            </button>
          </Form>
        )}
      </td>
      <td>
        <div className="sa-row">
          {template.enabled ? <Link to={`/app/agent/new?template=${encodeURIComponent(template.key)}`} className="sa-btn sa-btn-sm sa-btn-primary">Use</Link> : null}
          {!template.builtin ? <Link to={`?edit=${encodeURIComponent(template.key)}`} className="sa-btn sa-btn-sm" preventScrollReset>Edit</Link> : null}
          <Link to={`?duplicate=${encodeURIComponent(template.key)}`} className="sa-btn sa-btn-sm" preventScrollReset>Duplicate</Link>
          {!template.builtin ? (
            <Form
              method="post"
              className="sa-inline-form"
              preventScrollReset
              onSubmit={(event) => { if (!window.confirm(`Delete the template “${template.name}”? Tasks already made from it are kept.`)) event.preventDefault(); }}
            >
              <Hidden values={{ intent: "delete", key: template.key }} />
              <button type="submit" className="sa-btn sa-btn-sm sa-btn-critical" disabled={busy}>Delete</button>
            </Form>
          ) : null}
        </div>
      </td>
    </tr>
  );

  const table = (rows: Template[]) => (
    <div className="sa-table-wrap">
      <table className="sa-table">
        <thead><tr><th>Template</th><th>Type</th><th>Blanks to fill in</th><th>Shown</th><th /></tr></thead>
        <tbody>{rows.map(renderRow)}</tbody>
      </table>
    </div>
  );

  return (
    <>
      <LoadError error={data.error} />
      <ActionBanner result={result} />
      {savedTemplate && !result ? <Banner tone="success">Saved “{savedTemplate.name}”.</Banner> : null}
      {data.edit && editTarget?.builtin ? (
        <Banner tone="info">Built-in templates can&apos;t be changed. Duplicate it to make your own version.</Banner>
      ) : null}

      {mode ? <TemplateEditor mode={mode} taskTypes={data.taskTypes} /> : null}

      <s-section heading="Your templates">
        <div className="sa-spread" style={{ marginBottom: 10 }}>
          <p className="sa-muted">Your own task templates. Duplicate a built-in one to start from it.</p>
          <Link to="?new=1" className="sa-btn sa-btn-primary" preventScrollReset>New template</Link>
        </div>
        {own.length ? table(own) : (
          <EmptyState title="No templates of your own yet">
            Create one from scratch, or duplicate a built-in template below and change it.
          </EmptyState>
        )}
      </s-section>

      <s-section heading="Built-in templates">
        {builtins.length ? table(builtins) : <p className="sa-muted">None.</p>}
      </s-section>
    </>
  );
}
