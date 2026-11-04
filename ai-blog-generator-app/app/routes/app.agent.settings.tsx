import { useState } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData } from "react-router";

import { AgentTime, LoadError } from "../components/agent/parts";
import { ActionBanner, Badge, Banner, Card, EmptyState, Field, Hidden, Toggle, useBusy, usePolling } from "../components/social/ui";
import {
  TaskAgentError, deleteEventType, errorMessage, formBool, formInt, formString, listEventTypes, runAgentIntent, saveEventType,
  taFetch, testEventTypeEmail,
  type Account, type AccountSaveResponse, type AccountsResponse, type AgentActionResult, type BrowserResponse,
  type BrowserStatus, type EmailAccount, type EmailSaveResponse, type EmailTestResponse, type EventType,
  type EventTypesResponse, type MessageResponse, type SettingsResponse,
  type TelegramResponse, type TelegramStatus,
} from "../lib/task-agent.server";
import { seg } from "../lib/task-agent.shared";
import { authenticate } from "../shopify.server";

export const handle = { title: "Settings" };

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const url = new URL(request.url);
  const editSite = url.searchParams.get("account") || "";
  const newAccount = url.searchParams.get("new_account") === "1";
  const savedAccount = url.searchParams.get("saved_account") || "";
  const editEventType = url.searchParams.get("event_type") || "";
  const newEventType = url.searchParams.get("new_event_type") === "1";
  const addLoginFor = url.searchParams.get("add_login") || "";
  const savedEventType = url.searchParams.get("saved_event_type") || "";
  const savedTypeLogin = url.searchParams.get("saved_type_login") || "";
  const [settingsResult, accountsResult, telegramResult, eventTypesResult] = await Promise.allSettled([
    taFetch<SettingsResponse>("/settings"),
    taFetch<AccountsResponse>("/accounts"),
    taFetch<TelegramResponse>("/telegram"),
    listEventTypes(),
  ]);
  return {
    settings: settingsResult.status === "fulfilled" ? settingsResult.value : null,
    settingsError: settingsResult.status === "rejected" ? errorMessage(settingsResult.reason) : "",
    accounts: accountsResult.status === "fulfilled" ? accountsResult.value.accounts : ([] as Account[]),
    email: accountsResult.status === "fulfilled" ? accountsResult.value.email : (null as EmailAccount | null),
    accountsError: accountsResult.status === "rejected" ? errorMessage(accountsResult.reason) : "",
    browser: accountsResult.status === "fulfilled" ? accountsResult.value.browser ?? null : null,
    telegram: telegramResult.status === "fulfilled" ? telegramResult.value.telegram : (null as TelegramStatus | null),
    telegramError: telegramResult.status === "rejected" ? errorMessage(telegramResult.reason) : "",
    eventTypes: eventTypesResult.status === "fulfilled" ? eventTypesResult.value : (null as EventTypesResponse | null),
    eventTypesError: eventTypesResult.status === "rejected" ? errorMessage(eventTypesResult.reason) : "",
    editSite,
    newAccount,
    savedAccount,
    editEventType,
    newEventType,
    addLoginFor,
    savedEventType,
    savedTypeLogin,
  };
};

/** Page anchor for one event type's card (keys are slugs, but keep the id safe anyway). */
function typeAnchor(key: string): string {
  return `event-type-${key.replace(/[^a-z0-9_-]/gi, "-")}`;
}

type SettingsActionResult = AgentActionResult & { section?: string };

export const action = async ({ request }: ActionFunctionArgs): Promise<SettingsActionResult | Response> => {
  const { redirect } = await authenticate.admin(request);
  const form = await request.formData();
  const intent = String(form.get("intent") || "");

  if (intent === "pause" || intent === "resume") {
    const result = await runAgentIntent(intent, async () => {
      await taFetch<SettingsResponse>("/settings", { body: { paused: intent === "pause" } });
      return { message: intent === "pause" ? "Paused. The agent finishes its current step and won't start new tasks." : "Resumed. The agent will pick up queued tasks." };
    });
    return { ...result, section: "worker" };
  }

  if (intent === "save_settings") {
    const result = await runAgentIntent(intent, async () => {
      const saved = await taFetch<{ message: string }>("/settings", {
        body: {
          llm_model: formString(form, "llm_model"),
          llm_thinking: formBool(form, "llm_thinking"),
          max_steps: formInt(form, "max_steps", "Steps per task"),
          max_minutes: formInt(form, "max_minutes", "Minutes per task"),
          max_attempts: formInt(form, "max_attempts", "Tries per task"),
        },
      });
      return { message: saved.message || "Settings saved." };
    });
    return { ...result, section: "settings" };
  }

  if (intent === "save_email") {
    const result = await runAgentIntent(intent, async () => {
      const password = String(form.get("password") || "");
      const saved = await taFetch<EmailSaveResponse>("/email", {
        body: {
          username: formString(form, "username"),
          from_name: formString(form, "from_name"),
          imap_host: formString(form, "imap_host"),
          imap_port: formInt(form, "imap_port", "IMAP port"),
          smtp_host: formString(form, "smtp_host"),
          smtp_port: formInt(form, "smtp_port", "SMTP port"),
          ...(password ? { password } : {}),
        },
      });
      return { message: saved.message };
    });
    return { ...result, section: "email" };
  }

  if (intent === "save_telegram") {
    const result = await runAgentIntent(intent, async () => {
      const body: Record<string, string> = { owner_username: formString(form, "owner_username") };
      const token = formString(form, "token");
      if (token) body.token = token;
      const saved = await taFetch<TelegramResponse>("/telegram", { body, timeoutMs: 30_000 });
      return { message: saved.message || "Saved." };
    });
    return { ...result, section: "telegram" };
  }

  if (intent === "test_telegram" || intent === "unlink_telegram" || intent === "disconnect_telegram") {
    const path = { test_telegram: "/telegram/test", unlink_telegram: "/telegram/unlink", disconnect_telegram: "/telegram/disconnect" }[intent];
    const result = await runAgentIntent(intent, async () => {
      const done = await taFetch<TelegramResponse>(path, { method: "POST", timeoutMs: 30_000 });
      return { message: done.message || "Done." };
    });
    return { ...result, section: "telegram" };
  }

  if (intent === "test_email") {
    const result = await runAgentIntent(intent, async () => {
      const tested = await taFetch<EmailTestResponse>("/email/test", { method: "POST", timeoutMs: 60_000 });
      return tested.ok
        ? { message: tested.message || "Connected — the agent could log in to both incoming and outgoing mail." }
        : { ok: false, intent, error: tested.message || "The connection test failed." };
    });
    return { ...result, section: "email" };
  }

  // Logins added or deleted inside an event type's card report back in that card.
  const fromEventTypes = formString(form, "from") === "event_types";
  const formEventType = formString(form, "event_type");

  if (intent === "save_account") {
    let savedSite = "";
    let savedType = "";
    const result = await runAgentIntent(intent, async () => {
      const site = formString(form, "site");
      const password = String(form.get("password") || "");
      const loginUrl = formString(form, "login_url");
      if (loginUrl && !/^https?:\/\//i.test(loginUrl)) throw new TaskAgentError("The login page must start with https:// (or http://).");
      const saved = await taFetch<AccountSaveResponse>("/accounts", {
        body: {
          ...(site ? { site } : {}),
          label: formString(form, "label"),
          login_url: loginUrl,
          username: formString(form, "username"),
          notes: formString(form, "notes"),
          ...(password ? { password } : {}),
          // Only sent when the form offers it ("" = shared by every type).
          ...(form.has("event_type") ? { event_type: formEventType } : {}),
        },
      });
      savedSite = saved.account.site;
      savedType = saved.account.event_type ?? formEventType;
      return { message: saved.message };
    });
    if (result.ok && savedSite) {
      if (fromEventTypes && savedType) {
        return redirect(`/app/agent/settings?saved_type_login=${encodeURIComponent(savedSite)}#${typeAnchor(savedType)}`);
      }
      return redirect(`/app/agent/settings?saved_account=${encodeURIComponent(savedSite)}#logins`);
    }
    return fromEventTypes ? { ...result, section: "event_types", target: formEventType } : { ...result, section: "logins" };
  }

  if (intent === "delete_account") {
    const site = formString(form, "site");
    const result = await runAgentIntent(intent, async () => {
      const deleted = await taFetch<MessageResponse>(`/accounts/${seg(site)}/delete`, { method: "POST" });
      return { message: deleted.message };
    }, site);
    return fromEventTypes ? { ...result, section: "event_types", target: formEventType } : { ...result, section: "logins" };
  }

  if (intent === "save_event_type") {
    const key = formString(form, "key");
    let savedKey = "";
    const result = await runAgentIntent(intent, async () => {
      const name = formString(form, "name");
      if (!name) throw new TaskAgentError("Give the event type a name, e.g. Comedy.", { type: "validation" });
      const imapPassword = String(form.get("imap_password") || "");
      const saved = await saveEventType({
        ...(key ? { key } : {}),
        name,
        email: formString(form, "email"),
        imap_host: formString(form, "imap_host"),
        imap_port: formString(form, "imap_port") ? formInt(form, "imap_port", "IMAP port") : 993,
        imap_username: formString(form, "imap_username"),
        // Blank keeps the saved password.
        ...(imapPassword ? { imap_password: imapPassword } : {}),
        facebook_page: formString(form, "facebook_page"),
        linkedin_page: formString(form, "linkedin_page"),
        tag_handles: formString(form, "tag_handles"),
        audience: String(form.get("audience") || "").replace(/\r\n?/g, "\n").trim(),
        enabled: formBool(form, "enabled"),
      });
      savedKey = saved.event_type.key;
      return { message: saved.message };
    }, key);
    if (result.ok && savedKey) {
      return redirect(`/app/agent/settings?saved_event_type=${encodeURIComponent(savedKey)}#${typeAnchor(savedKey)}`);
    }
    return { ...result, section: "event_types" };
  }

  if (intent === "test_event_type_email") {
    const key = formString(form, "key");
    const result = await runAgentIntent(intent, async () => {
      if (!key) throw new TaskAgentError("No event type was given.", { type: "validation" });
      const tested = await testEventTypeEmail(key);
      return tested.ok
        ? { message: tested.message || "Connected. The agent can log in to this inbox." }
        : { ok: false, intent, error: tested.message || "The agent couldn't log in to this inbox." };
    }, key);
    return { ...result, section: "event_types" };
  }

  if (intent === "delete_event_type") {
    const key = formString(form, "key");
    const result = await runAgentIntent(intent, async () => {
      if (!key) throw new TaskAgentError("No event type was given.", { type: "validation" });
      const deleted = await deleteEventType(key);
      return { message: deleted.message };
    }, key);
    return { ...result, section: "event_types" };
  }

  if (intent === "open_browser") {
    const result = await runAgentIntent(intent, async () => {
      let url = formString(form, "url");
      // "meetup.com" is fine too: add the https:// for them.
      if (url && !/^https?:\/\//i.test(url)) {
        if (/\s/.test(url) || !url.includes(".")) throw new TaskAgentError("That doesn't look like a web address. Try something like https://www.meetup.com/login/, or leave it blank.");
        url = `https://${url}`;
      }
      const opened = await taFetch<BrowserResponse>("/browser/open", { body: url ? { url } : {}, timeoutMs: 45_000 });
      return { message: opened.message || (opened.browser?.mode === "chrome" ? "Opened it in a new tab in your Chrome." : "The shared browser is open on the Mac.") };
    });
    return { ...result, section: "logins" };
  }

  if (intent === "login_window") {
    const site = formString(form, "site");
    const result = await runAgentIntent(intent, async () => {
      const opened = await taFetch<AccountSaveResponse>(`/accounts/${seg(site)}/login-window`, { method: "POST", timeoutMs: 45_000 });
      return { message: opened.message };
    }, site);
    return { ...result, section: "logins" };
  }

  return { ok: false, intent, error: `Unknown action “${intent}”.` };
};

function SectionResult({ result, section }: { result?: SettingsActionResult; section: string }) {
  if (!result || result.section !== section) return null;
  return <ActionBanner result={result} />;
}

function WorkerSection({ settings, result }: { settings: SettingsResponse; result?: SettingsActionResult }) {
  const { busy, isSubmitting } = useBusy();
  const paused = settings.settings.paused;
  return (
    <s-section heading="Worker">
      <SectionResult result={result} section="worker" />
      <div className="sa-spread">
        <div className="sa-stack-sm">
          <div className="sa-row">
            {paused ? <Badge tone="warning">Paused</Badge> : <Badge tone="success">Running</Badge>}
          </div>
          <span className="sa-small sa-muted">
            {paused
              ? "The agent won't start any new tasks. Queued tasks wait until you resume."
              : "The agent picks up queued tasks one at a time. Pause it if you need the Mac's AI model for something else."}
          </span>
        </div>
        <Form method="post" className="sa-inline-form" preventScrollReset>
          <Hidden values={{ intent: paused ? "resume" : "pause" }} />
          <button type="submit" className={`sa-btn ${paused ? "sa-btn-success" : ""}`} disabled={busy}>
            {isSubmitting(paused ? "resume" : "pause") ? (paused ? "Resuming…" : "Pausing…") : paused ? "Resume" : "Pause"}
          </button>
        </Form>
      </div>
    </s-section>
  );
}

function ModelSection({ settings, result }: { settings: SettingsResponse; result?: SettingsActionResult }) {
  const { busy, isSubmitting } = useBusy();
  const current = settings.settings;
  const [model, setModel] = useState(current.llm_model);
  const models = settings.models.includes(current.llm_model) ? settings.models : [current.llm_model, ...settings.models];
  const limits = settings.limits;

  return (
    <s-section heading="Model and limits">
      <SectionResult result={result} section="settings" />
      <Form method="post" className="sa-stack">
        <Hidden values={{ intent: "save_settings" }} />
        <Field label="AI model" help="The local model on the Mac the agent uses for every task.">
          <select name="llm_model" value={model} onChange={(event) => setModel(event.target.value)}>
            {models.map((id) => (
              <option key={id} value={id}>{id}{id === settings.recommended_model ? " (recommended)" : ""}</option>
            ))}
          </select>
        </Field>
        {model !== settings.recommended_model ? (
          <Banner tone="warning" title="Use the same model as Social Autopilot">
            Use the same model as Social Autopilot ({settings.recommended_model}) so they never compete for memory. Two
            large models can&apos;t fit on the Mac at once, and a busy one blocks the other — that&apos;s what made a
            campaign run fail before.
          </Banner>
        ) : null}
        <Toggle
          name="llm_thinking"
          checked={current.llm_thinking}
          label="Let the agent think before it answers"
          help="Slower, but far more careful: in testing it caught facts it otherwise got wrong. Social Autopilot isn't affected."
        />
        <div className="sa-grid-sm">
          <Field label="Steps per task" help={`${limits.max_steps[0]}–${limits.max_steps[1]}. Stops a task that goes round in circles.`}>
            <input type="number" name="max_steps" defaultValue={current.max_steps} min={limits.max_steps[0]} max={limits.max_steps[1]} step={1} required />
          </Field>
          <Field label="Minutes per task" help={`${limits.max_minutes[0]}–${limits.max_minutes[1]}.`}>
            <input type="number" name="max_minutes" defaultValue={current.max_minutes} min={limits.max_minutes[0]} max={limits.max_minutes[1]} step={1} required />
          </Field>
          <Field label="Tries per task" help={`${limits.max_attempts[0]}–${limits.max_attempts[1]}. Retries when the model or network has a hiccup.`}>
            <input type="number" name="max_attempts" defaultValue={current.max_attempts} min={limits.max_attempts[0]} max={limits.max_attempts[1]} step={1} required />
          </Field>
        </div>
        <div className="sa-form-actions">
          <button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>{isSubmitting("save_settings") ? "Saving…" : "Save"}</button>
          <span className="sa-small sa-muted">Changes apply from the next task.</span>
        </div>
      </Form>
    </s-section>
  );
}

function PasswordField({ hasPassword, label = "Password", help }: { hasPassword: boolean; label?: string; help?: string }) {
  return (
    <Field
      label={label}
      help={help || (hasPassword
        ? "Saved in the Mac's Keychain. Leave blank to keep it; type a new one to replace it."
        : "Saved in the Mac's Keychain, never in a database, and never shown again.")}
    >
      <input type="password" name="password" autoComplete="new-password" placeholder={hasPassword ? "Saved in Keychain" : ""} />
    </Field>
  );
}

function EmailSection({ email, result }: { email: EmailAccount; result?: SettingsActionResult }) {
  const { busy, isSubmitting } = useBusy();
  const configured = Boolean(email.username && email.imap_host && email.smtp_host && email.has_password);
  return (
    <s-section heading="Email account">
      <SectionResult result={result} section="email" />
      <p className="sa-muted" style={{ marginBottom: 10 }}>
        The inbox the agent reads and replies from. It never sends anything without your approval. For Gmail, use an{" "}
        <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer">app password</a>{" "}
        (imap.gmail.com 993, smtp.gmail.com 587).
      </p>
      <Form method="post" className="sa-stack">
        <Hidden values={{ intent: "save_email" }} />
        <div className="sa-grid-2">
          <Field label="Email address (login)"><input type="email" name="username" defaultValue={email.username} autoComplete="off" /></Field>
          <Field label="Name on sent emails" help="e.g. Joe at BioluxeLab"><input type="text" name="from_name" defaultValue={email.from_name} /></Field>
          <Field label="Incoming mail server (IMAP)"><input type="text" name="imap_host" defaultValue={email.imap_host} placeholder="imap.gmail.com" /></Field>
          <Field label="IMAP port"><input type="number" name="imap_port" defaultValue={email.imap_port || 993} min={1} max={65535} required /></Field>
          <Field label="Outgoing mail server (SMTP)"><input type="text" name="smtp_host" defaultValue={email.smtp_host} placeholder="smtp.gmail.com" /></Field>
          <Field label="SMTP port"><input type="number" name="smtp_port" defaultValue={email.smtp_port || 587} min={1} max={65535} required /></Field>
          <PasswordField hasPassword={email.has_password} />
        </div>
        <div className="sa-form-actions">
          <button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>{isSubmitting("save_email") ? "Saving…" : "Save email account"}</button>
        </div>
      </Form>
      <div className="sa-spread" style={{ marginTop: 12 }}>
        <span className="sa-small">
          {email.last_test_at ? (
            <>
              Last test <AgentTime iso={email.last_test_at} relative />:{" "}
              {email.last_test_ok ? <Badge tone="success">Worked</Badge> : <Badge tone="critical">Failed</Badge>}
              {email.last_test_message ? <span className="sa-muted"> {email.last_test_message}</span> : null}
            </>
          ) : <span className="sa-muted">Not tested yet.</span>}
        </span>
        <Form method="post" className="sa-inline-form" preventScrollReset>
          <Hidden values={{ intent: "test_email" }} />
          <button type="submit" className="sa-btn" disabled={busy || !configured} title={configured ? "Logs in to both servers; sends nothing" : "Save the address, servers and password first"}>
            {isSubmitting("test_email") ? "Testing… (up to a minute)" : "Test connection"}
          </button>
        </Form>
      </div>
    </s-section>
  );
}

function TelegramSection({ telegram, result }: { telegram: TelegramStatus; result?: SettingsActionResult }) {
  const { busy, isSubmitting } = useBusy();
  const bot = telegram.bot_username ? `@${telegram.bot_username}` : "your bot";
  return (
    <s-section heading="Telegram">
      <SectionResult result={result} section="telegram" />
      <div className="sa-row" style={{ marginBottom: 10 }}>
        {telegram.configured
          ? <Badge tone="success">Bot connected{telegram.bot_username ? `: @${telegram.bot_username}` : ""}</Badge>
          : <Badge tone="neutral">No bot yet</Badge>}
        {telegram.linked
          ? <Badge tone="success">Talking to @{telegram.linked_username || telegram.owner_username}</Badge>
          : telegram.configured ? <Badge tone="warning">Waiting for /start from @{telegram.owner_username || "…"}</Badge> : null}
        {!telegram.social_connected ? <Badge tone="critical">Social Autopilot key missing</Badge> : null}
      </div>
      <p className="sa-muted" style={{ marginBottom: 10 }}>
        Approve posts and emails, ask questions (“what&apos;s posting tomorrow?”) and give instructions from your phone.
        Nothing changes until you press Confirm in Telegram. You&apos;ll get posts and emails waiting for approval,
        failures, and a summary every morning at {String(telegram.summary_hour).padStart(2, "0")}:00.
      </p>
      {telegram.configured && !telegram.linked ? (
        <Banner tone="info" title="Last step">
          Open <strong>{bot}</strong> in Telegram while signed in as <strong>@{telegram.owner_username}</strong> and send <code>/start</code>.
          This page updates by itself once it&apos;s linked.
        </Banner>
      ) : null}
      {telegram.last_error ? <Banner tone="warning" title="Telegram problem">{telegram.last_error}</Banner> : null}
      <Form method="post" className="sa-stack">
        <Hidden values={{ intent: "save_telegram" }} />
        <div className="sa-grid-2">
          <Field
            label="Bot token"
            help={telegram.configured
              ? "Saved in the Mac's Keychain. Leave blank to keep it; paste a new one to replace it."
              : "From @BotFather in Telegram (/newbot). Saved in the Mac's Keychain and never shown again."}
          >
            <input type="password" name="token" autoComplete="off" placeholder={telegram.configured ? "Saved in Keychain" : "123456789:AA…"} />
          </Field>
          <Field label="Your Telegram username" help="Only this account can use the bot. It locks to your account on the first /start.">
            <input type="text" name="owner_username" defaultValue={telegram.owner_username} placeholder="JoeBains" required />
          </Field>
        </div>
        <div className="sa-form-actions">
          <button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>{isSubmitting("save_telegram") ? "Checking with Telegram…" : "Save"}</button>
        </div>
      </Form>
      {telegram.configured ? (
        <div className="sa-row" style={{ marginTop: 12 }}>
          <Form method="post" className="sa-inline-form" preventScrollReset>
            <Hidden values={{ intent: "test_telegram" }} />
            <button type="submit" className="sa-btn" disabled={busy || !telegram.linked}>{isSubmitting("test_telegram") ? "Sending…" : "Send a test message"}</button>
          </Form>
          {telegram.linked ? (
            <Form method="post" className="sa-inline-form" preventScrollReset onSubmit={(event) => { if (!window.confirm("Unlink your Telegram account? The next /start from your username links it again.")) event.preventDefault(); }}>
              <Hidden values={{ intent: "unlink_telegram" }} />
              <button type="submit" className="sa-btn" disabled={busy}>Unlink</button>
            </Form>
          ) : null}
          <Form method="post" className="sa-inline-form" preventScrollReset onSubmit={(event) => { if (!window.confirm("Disconnect the bot? Its token is removed from the Keychain.")) event.preventDefault(); }}>
            <Hidden values={{ intent: "disconnect_telegram" }} />
            <button type="submit" className="sa-btn sa-btn-critical" disabled={busy}>Disconnect</button>
          </Form>
        </div>
      ) : null}
    </s-section>
  );
}

function AccountEditor({ account, eventTypes }: { account: Account | null; eventTypes: EventType[] | null }) {
  const { busy, isSubmitting } = useBusy();
  const currentType = account?.event_type || "";
  const lostType = currentType && !(eventTypes || []).some((eventType) => eventType.key === currentType);
  return (
    <Card accent="info">
      <Form method="post" className="sa-stack" key={account?.site || "new"}>
        <Hidden values={{ intent: "save_account", site: account?.site || "" }} />
        <strong>{account ? `Edit ${account.label}` : "Add a site login"}</strong>
        <div className="sa-grid-2">
          <Field label="Site name" help="e.g. Eventbrite, Meetup, Facebook">
            <input type="text" name="label" required maxLength={60} defaultValue={account?.label || ""} />
          </Field>
          <Field label="Login page" help="The page where you sign in, e.g. https://www.eventbrite.co.uk/signin/">
            <input type="url" name="login_url" required defaultValue={account?.login_url || ""} placeholder="https://" />
          </Field>
          <Field label="Username or email"><input type="text" name="username" defaultValue={account?.username || ""} autoComplete="off" /></Field>
          <PasswordField hasPassword={Boolean(account?.has_password)} />
          {eventTypes ? (
            <Field label="Used for" help="Facebook and LinkedIn are shared by every type. Eventbrite and Meetup logins belong to one type.">
              <select name="event_type" defaultValue={currentType}>
                <option value="">All event types</option>
                {eventTypes.map((eventType) => <option key={eventType.key} value={eventType.key}>{eventType.name}</option>)}
                {lostType ? <option value={currentType}>{account?.event_type_name || currentType}</option> : null}
              </select>
            </Field>
          ) : null}
        </div>
        <Field label="Notes (optional)" help="Anything the agent should know, e.g. which page or group to use.">
          <textarea name="notes" rows={2} defaultValue={account?.notes || ""} />
        </Field>
        <div className="sa-form-actions">
          <button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>{isSubmitting("save_account") ? "Saving…" : "Save login"}</button>
          <Link to="/app/agent/settings#logins" className="sa-btn sa-btn-plain">Cancel</Link>
        </div>
      </Form>
    </Card>
  );
}

// ------------------------------------------------------------------ event types

/** Ready-made logins for an event type (the login page and username are filled in for you). */
const TYPE_LOGIN_PRESETS: Record<string, { label: string; url: string }> = {
  eventbrite: { label: "Eventbrite", url: "https://www.eventbrite.co.uk/signin/" },
  meetup: { label: "Meetup", url: "https://www.meetup.com/login/" },
};

/** A Page given as a name or a link: links open in a new tab (http(s) only). */
function PageText({ value }: { value: string }) {
  const text = (value || "").trim();
  if (!text) return <span className="sa-muted">—</span>;
  if (/^https?:\/\//i.test(text)) return <a href={text} target="_blank" rel="noopener noreferrer" className="sa-break">{text}</a>;
  return <span className="sa-break">{text}</span>;
}

function InboxStatus({ eventType }: { eventType: EventType }) {
  const when = eventType.last_test_at ? <span className="sa-small sa-muted"> (<AgentTime iso={eventType.last_test_at} relative />)</span> : null;
  if (eventType.last_test_ok === true) return <span><Badge tone="success">Connected ✓</Badge>{when}</span>;
  if (eventType.last_test_ok === false) {
    return (
      <span>
        <Badge tone="critical">Failed</Badge>
        {eventType.last_test_message ? <span className="sa-small"> {eventType.last_test_message}</span> : null}
        {when}
      </span>
    );
  }
  return <Badge tone="neutral" title="Press “Test email” to check the agent can log in">Not tested</Badge>;
}

function EventTypeEditor({ eventType }: { eventType: EventType | null }) {
  const { busy, isSubmitting } = useBusy();
  const key = eventType?.key || "";
  return (
    <Card accent="info">
      <Form method="post" className="sa-stack" key={key || "new"} preventScrollReset>
        <Hidden values={{ intent: "save_event_type", key }} />
        <strong>{eventType ? `Edit ${eventType.name}` : "Add an event type"}</strong>
        <div className="sa-grid-2">
          <Field label="Name" help="e.g. Comedy, Biohacking">
            <input type="text" name="name" required maxLength={60} defaultValue={eventType?.name || ""} />
          </Field>
          <Field label="Email" help="The address you use to sign up and run this kind of event.">
            <input type="email" name="email" defaultValue={eventType?.email || ""} autoComplete="off" />
          </Field>
          <Field label="IMAP host" help="So the agent can read sign-up codes sent to this email.">
            <input type="text" name="imap_host" defaultValue={eventType?.imap_host || ""} placeholder="imap.gmail.com" />
          </Field>
          <Field label="IMAP port">
            <input type="number" name="imap_port" defaultValue={eventType?.imap_port || 993} min={1} max={65535} step={1} required />
          </Field>
          <Field label="IMAP username">
            <input type="text" name="imap_username" defaultValue={eventType?.imap_username || ""} placeholder="same as email" autoComplete="off" />
          </Field>
          <Field
            label="IMAP password"
            help={<>Leave blank to keep the saved one. For Gmail use an{" "}
              <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer">app password</a>.</>}
          >
            <input type="password" name="imap_password" autoComplete="new-password" placeholder={eventType?.has_imap_password ? "Saved" : ""} />
          </Field>
          <Field label="Facebook Page" help="Name or link">
            <input type="text" name="facebook_page" defaultValue={eventType?.facebook_page || ""} />
          </Field>
          <Field label="LinkedIn Page" help="Name or link">
            <input type="text" name="linkedin_page" defaultValue={eventType?.linkedin_page || ""} />
          </Field>
          <Field label="Handles to tag" help="e.g. @thevenue @headliner">
            <input type="text" name="tag_handles" defaultValue={eventType?.tag_handles || ""} />
          </Field>
        </div>
        <Field label="Audience and notes" help="Who it's for, tone, places that work well">
          <textarea name="audience" rows={3} defaultValue={eventType?.audience || ""} />
        </Field>
        <Toggle
          name="enabled"
          checked={eventType ? eventType.enabled : true}
          label="Enabled"
          help="Switch off to hide it when you start a task."
        />
        <div className="sa-form-actions">
          <button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>
            {isSubmitting("save_event_type", ["key", key]) ? "Saving…" : "Save"}
          </button>
          <Link to={`/app/agent/settings#${key ? typeAnchor(key) : "event-types"}`} className="sa-btn sa-btn-plain" preventScrollReset>Cancel</Link>
        </div>
      </Form>
    </Card>
  );
}

function TypeLoginForm({ eventType }: { eventType: EventType }) {
  const { busy, isSubmitting } = useBusy();
  const taken = new Set((eventType.logins || []).map((login) => login.label.trim().toLowerCase()));
  const first = !taken.has("eventbrite") ? "eventbrite" : !taken.has("meetup") ? "meetup" : "other";
  const [choice, setChoice] = useState(first);
  const [loginUrl, setLoginUrl] = useState(TYPE_LOGIN_PRESETS[first]?.url || "");
  const preset = TYPE_LOGIN_PRESETS[choice];
  return (
    <Card accent="info">
      <Form method="post" className="sa-stack" preventScrollReset>
        <Hidden values={{ intent: "save_account", event_type: eventType.key, from: "event_types", ...(preset ? { label: preset.label } : {}) }} />
        <strong>Add a login for {eventType.name}</strong>
        <div className="sa-grid-2">
          <Field label="Site">
            <select
              value={choice}
              onChange={(event) => {
                setChoice(event.target.value);
                setLoginUrl(TYPE_LOGIN_PRESETS[event.target.value]?.url || "");
              }}
            >
              <option value="eventbrite">Eventbrite</option>
              <option value="meetup">Meetup</option>
              <option value="other">Other</option>
            </select>
          </Field>
          {!preset ? (
            <Field label="Site name" help="e.g. Allevents, Skiddle">
              <input type="text" name="label" required maxLength={60} />
            </Field>
          ) : null}
          <Field label="Login page" help="The page where you sign in.">
            <input type="url" name="login_url" required value={loginUrl} onChange={(event) => setLoginUrl(event.target.value)} placeholder="https://" />
          </Field>
          <Field label="Username or email">
            <input type="text" name="username" defaultValue={eventType.email} autoComplete="off" />
          </Field>
          <PasswordField hasPassword={false} />
        </div>
        <div className="sa-form-actions">
          <button type="submit" className="sa-btn sa-btn-primary" disabled={busy}>
            {isSubmitting("save_account", ["event_type", eventType.key]) ? "Saving…" : "Save login"}
          </button>
          <Link to={`/app/agent/settings#${typeAnchor(eventType.key)}`} className="sa-btn sa-btn-plain" preventScrollReset>Cancel</Link>
        </div>
      </Form>
    </Card>
  );
}

function TypeLogins({ eventType, adding, savedLogin }: { eventType: EventType; adding: boolean; savedLogin: string }) {
  const { busy, isSubmitting } = useBusy();
  const logins = eventType.logins || [];
  const saved = savedLogin ? logins.find((login) => login.site === savedLogin) : undefined;
  return (
    <div className="sa-stack-sm">
      <div className="sa-spread">
        <strong>Logins</strong>
        {!adding ? (
          <Link to={`?add_login=${encodeURIComponent(eventType.key)}#${typeAnchor(eventType.key)}`} className="sa-btn sa-btn-sm" preventScrollReset>
            Add login
          </Link>
        ) : null}
      </div>
      {saved ? <Banner tone="success">Saved the {saved.label} login for {eventType.name}.</Banner> : null}
      {logins.length ? (
        <div className="sa-table-wrap">
          <table className="sa-table">
            <thead><tr><th>Site</th><th>Username</th><th>Password saved</th><th /></tr></thead>
            <tbody>
              {logins.map((login) => (
                <tr key={login.site}>
                  <td className="sa-break">
                    <strong>{login.label}</strong>
                    {login.login_url ? <div className="sa-small"><a href={login.login_url} target="_blank" rel="noopener noreferrer">{login.login_url}</a></div> : null}
                  </td>
                  <td className="sa-break">{login.username || <span className="sa-muted">—</span>}</td>
                  <td>{login.has_password ? <Badge tone="success">Yes</Badge> : <Badge tone="neutral">No</Badge>}</td>
                  <td>
                    <div className="sa-row">
                      <Link to={`?account=${encodeURIComponent(login.site)}#logins`} className="sa-btn sa-btn-sm" preventScrollReset>Edit</Link>
                      <Form
                        method="post"
                        className="sa-inline-form"
                        preventScrollReset
                        onSubmit={(event) => {
                          if (!window.confirm(`Delete the ${login.label} login for ${eventType.name}? Its saved username and password are removed.`)) event.preventDefault();
                        }}
                      >
                        <Hidden values={{ intent: "delete_account", site: login.site, event_type: eventType.key, from: "event_types" }} />
                        <button type="submit" className="sa-btn sa-btn-sm sa-btn-critical" disabled={busy}>
                          {isSubmitting("delete_account", ["site", login.site]) ? "Deleting…" : "Delete"}
                        </button>
                      </Form>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : !adding ? (
        <p className="sa-small sa-muted">No logins yet. Add this type&apos;s Eventbrite and Meetup logins.</p>
      ) : null}
      {adding ? <TypeLoginForm eventType={eventType} /> : null}
    </div>
  );
}

function EventTypeCard({ eventType, editing, adding, justSaved, savedLogin, result }: {
  eventType: EventType; editing: boolean; adding: boolean; justSaved: boolean; savedLogin: string; result?: SettingsActionResult;
}) {
  const { busy, isSubmitting } = useBusy();
  const key = eventType.key;
  const logins = eventType.logins || [];
  const canTest = Boolean(eventType.imap_host && (eventType.imap_username || eventType.email) && eventType.has_imap_password);
  const deleteText = `Delete the ${eventType.name} event type? Its email settings and its ${logins.length} login${logins.length === 1 ? "" : "s"} `
    + "(with their saved passwords) are removed too. Shared logins like Facebook and LinkedIn stay.";
  return (
    <div id={typeAnchor(key)}>
      <Card muted={!eventType.enabled}>
        <div className="sa-stack">
          {result ? <ActionBanner result={result} /> : null}
          {justSaved && !result ? (
            <Banner tone="success">
              Saved {eventType.name}.{eventType.last_test_ok !== true && canTest ? " Press “Test email” to check the agent can read its inbox." : ""}
            </Banner>
          ) : null}
          <div className="sa-spread">
            <div className="sa-row">
              <strong>{eventType.name}</strong>
              {!eventType.enabled ? <Badge tone="neutral" title="Hidden when you start a task">Off</Badge> : null}
            </div>
            <div className="sa-row">
              {!editing ? (
                <Link to={`?event_type=${encodeURIComponent(key)}#${typeAnchor(key)}`} className="sa-btn sa-btn-sm" preventScrollReset>Edit</Link>
              ) : null}
              <Form method="post" className="sa-inline-form" preventScrollReset>
                <Hidden values={{ intent: "test_event_type_email", key }} />
                <button
                  type="submit"
                  className="sa-btn sa-btn-sm"
                  disabled={busy || !canTest}
                  title={canTest ? "Logs in to this inbox with the saved details; reads nothing" : "Save the email, IMAP host and password first"}
                >
                  {isSubmitting("test_event_type_email", ["key", key]) ? "Testing… (up to a minute)" : "Test email"}
                </button>
              </Form>
              <Form
                method="post"
                className="sa-inline-form"
                preventScrollReset
                onSubmit={(event) => { if (!window.confirm(deleteText)) event.preventDefault(); }}
              >
                <Hidden values={{ intent: "delete_event_type", key }} />
                <button type="submit" className="sa-btn sa-btn-sm sa-btn-critical" disabled={busy}>
                  {isSubmitting("delete_event_type", ["key", key]) ? "Deleting…" : "Delete"}
                </button>
              </Form>
            </div>
          </div>
          <dl className="sa-kv">
            <dt>Email</dt><dd className="sa-break">{eventType.email || <span className="sa-muted">—</span>}</dd>
            <dt>Inbox</dt><dd><InboxStatus eventType={eventType} /></dd>
            <dt>Facebook Page</dt><dd><PageText value={eventType.facebook_page} /></dd>
            <dt>LinkedIn Page</dt><dd><PageText value={eventType.linkedin_page} /></dd>
            {eventType.tag_handles ? <><dt>Tags</dt><dd className="sa-break">{eventType.tag_handles}</dd></> : null}
            <dt>Logins</dt><dd>{logins.length}</dd>
          </dl>
          {editing ? <EventTypeEditor eventType={eventType} /> : null}
          <TypeLogins eventType={eventType} adding={adding} savedLogin={savedLogin} />
        </div>
      </Card>
    </div>
  );
}

function EventTypesSection({ data, error, editKey, newType, addLoginFor, savedEventType, savedTypeLogin, result }: {
  data: EventTypesResponse | null; error: string; editKey: string; newType: boolean; addLoginFor: string;
  savedEventType: string; savedTypeLogin: string; result?: SettingsActionResult;
}) {
  const types = data?.event_types || [];
  const shared = data?.shared_logins || [];
  const mine = result?.section === "event_types" ? result : undefined;
  // A result for one type shows in its card; anything else (a new type, a deleted one) shows at the top.
  const inCard = mine?.target ? types.some((eventType) => eventType.key === mine.target) : false;
  return (
    <s-section heading="Event types">
      <div id="event-types" />
      {mine && !inCard ? <ActionBanner result={mine} /> : null}
      {!data ? <LoadError error={error} /> : (
        <>
          <div className="sa-spread" style={{ marginBottom: 10 }}>
            <div className="sa-stack-sm" style={{ maxWidth: 640 }}>
              <p className="sa-muted">
                Each kind of event you run has its own email, logins and Pages. When you start a &ldquo;Promote an event&rdquo; task you pick
                the type, and the agent uses its details.
              </p>
              <p className="sa-small sa-muted">
                {shared.length
                  ? <>Shared by every type: {shared.map((login) => login.label).join(", ")} (one login reaches all your Pages). Change these under <a href="#logins">Site logins</a>.</>
                  : <>Facebook and LinkedIn use one shared login for every type. Add it under <a href="#logins">Site logins</a>, set to &ldquo;All event types&rdquo;.</>}
              </p>
            </div>
            {!newType ? <Link to="?new_event_type=1#event-types" className="sa-btn sa-btn-primary" preventScrollReset>Add event type</Link> : null}
          </div>
          <div className="sa-stack">
            {newType ? <EventTypeEditor eventType={null} /> : null}
            {types.map((eventType) => (
              <EventTypeCard
                key={eventType.key}
                eventType={eventType}
                editing={editKey === eventType.key}
                adding={addLoginFor === eventType.key}
                justSaved={savedEventType === eventType.key}
                savedLogin={savedTypeLogin}
                result={mine && inCard && mine.target === eventType.key ? mine : undefined}
              />
            ))}
            {!types.length && !newType ? (
              <EmptyState title="No event types yet">
                Add one for each kind of event you run, e.g. Comedy, Bloomsbury speakers, Biohacking, Juggling.
              </EmptyState>
            ) : null}
          </div>
        </>
      )}
    </s-section>
  );
}

function BrowserState({ browser }: { browser: BrowserStatus }) {
  const loginTabs = browser.login_tabs || [];
  if (browser.mode === "chrome") {
    return (
      <div className="sa-row sa-small">
        {browser.offline ? <Badge tone="critical">Chrome not connected</Badge> : <Badge tone="success">Connected to your Chrome</Badge>}
        {browser.open_tabs ? <span className="sa-muted">{browser.open_tabs} agent tab{browser.open_tabs === 1 ? "" : "s"} open</span> : null}
        {loginTabs.length ? <Badge tone="attention" title="Log in in Chrome, then close the tab">Login tabs open: {loginTabs.join(", ")}</Badge> : null}
        {browser.busy_job_id ? (
          <span className="sa-muted">In use by <Link to={`/app/agent/tasks/${seg(browser.busy_job_id)}`}>task {browser.busy_job_id}</Link></span>
        ) : null}
      </div>
    );
  }
  return (
    <div className="sa-row sa-small">
      {!browser.running ? (
        <Badge tone="neutral" title="It starts by itself when a task or a login needs it">Shared browser closed</Badge>
      ) : browser.visible ? (
        <Badge tone="success">Shared browser open on the Mac</Badge>
      ) : (
        <Badge tone="info" title="An agent is using it out of sight">Shared browser running in the background</Badge>
      )}
      {browser.running && browser.open_tabs ? <span className="sa-muted">{browser.open_tabs} tab{browser.open_tabs === 1 ? "" : "s"}</span> : null}
      {loginTabs.length ? <Badge tone="attention" title="Log in on the Mac, then close the tab">Login tabs open: {loginTabs.join(", ")}</Badge> : null}
      {browser.busy_job_id ? (
        <span className="sa-muted">In use by <Link to={`/app/agent/tasks/${seg(browser.busy_job_id)}`}>task {browser.busy_job_id}</Link></span>
      ) : null}
      {!browser.profile_saved ? <span className="sa-muted">No logins saved yet</span> : null}
    </div>
  );
}

function SharedBrowserPanel({ browser }: { browser: BrowserStatus | null }) {
  const { busy, isSubmitting } = useBusy();
  const chrome = browser?.mode === "chrome";
  return (
    <Card muted>
      <div className="sa-stack-sm">
        <strong>{chrome ? "Your Chrome" : "Shared browser"}</strong>
        <p className="sa-small">
          {chrome
            ? "Agents work in your everyday Chrome through the OpenClaw extension, using the logins already there. They open and close only their own tabs."
            : "All agents share one browser. Log in to a site once (here or when a task asks you on Telegram) and every task can use it."}
        </p>
        {chrome && browser?.offline ? (
          <Banner tone="warning" title="Chrome isn't connected">
            Open Chrome and check the OpenClaw extension is on.
            <div className="sa-small sa-muted">{browser.offline}</div>
          </Banner>
        ) : null}
        {browser ? <BrowserState browser={browser} /> : null}
        <Form method="post" className="sa-row" preventScrollReset>
          <Hidden values={{ intent: "open_browser" }} />
          <input
            type="text"
            name="url"
            inputMode="url"
            placeholder="Page to open (optional), e.g. https://www.meetup.com/login/"
            aria-label="Page to open (optional)"
            style={{ flex: "1 1 260px", minWidth: 0 }}
          />
          <button
            type="submit"
            className="sa-btn"
            disabled={busy}
            title={chrome ? "Opens the page in a new tab in your Chrome" : "Shows the shared browser on the Mac so you can log in to anything"}
          >
            {isSubmitting("open_browser") ? "Opening…" : chrome ? "Open in Chrome" : "Open shared browser"}
          </button>
        </Form>
      </div>
    </Card>
  );
}

function LoginsSection({ accounts, eventTypes, browser, editSite, newAccount, savedAccount, result }: {
  accounts: Account[]; eventTypes: EventType[] | null; browser: BrowserStatus | null; editSite: string; newAccount: boolean;
  savedAccount: string; result?: SettingsActionResult;
}) {
  const { busy, isSubmitting } = useBusy();
  const editing = editSite ? accounts.find((account) => account.site === editSite) || null : null;
  const showEditor = newAccount || Boolean(editing);
  const chrome = browser?.mode === "chrome";
  // Older backends don't say which event type a login is for: hide the column then.
  const showTypes = accounts.some((account) => typeof account.event_type === "string");
  const loginWindowHelp = chrome
    ? "Opens this site's login page in a new tab in your Chrome. Log in by hand if you need to, then close the tab."
    : "Opens this site's login page as a tab in the shared browser on the Mac. Log in by hand, including any 2-step code, then close the tab.";

  return (
    <s-section heading="Site logins">
      <div id="logins" />
      <SectionResult result={result} section="logins" />
      {savedAccount && !result ? (
        <Banner tone="success">
          Saved the {accounts.find((account) => account.site === savedAccount)?.label || savedAccount} login.
          {accounts.find((account) => account.site === savedAccount)?.has_profile
            ? ""
            : chrome
              ? " If you're not logged in to it in Chrome yet, click “Open login window” and log in once."
              : " Next: click “Open login window” and log in once in the shared browser on the Mac."}
        </Banner>
      ) : null}
      <div className="sa-spread" style={{ marginBottom: 10 }}>
        <p className="sa-muted" style={{ maxWidth: 640 }}>
          {chrome ? (
            <>
              Accounts the agent can use, like a member of staff would. Sites you&apos;re already logged in to in Chrome just work.
              &ldquo;Open login window&rdquo; opens a site&apos;s login page in a new tab in your Chrome if you need to log in.
            </>
          ) : (
            <>
              Accounts the agent can use, like a member of staff would. &ldquo;Open login window&rdquo; opens that site&apos;s login page as a
              tab in the shared browser on the Mac. Log in by hand, including any 2-step code, then close the tab.
            </>
          )}
        </p>
        {!showEditor ? <Link to="?new_account=1#logins" className="sa-btn sa-btn-primary" preventScrollReset>Add a site login</Link> : null}
      </div>
      <div style={{ marginBottom: 12 }}><SharedBrowserPanel browser={browser} /></div>
      {showEditor ? <div style={{ marginBottom: 12 }}><AccountEditor account={editing} eventTypes={eventTypes} /></div> : null}
      {accounts.length ? (
        <div className="sa-table-wrap">
          <table className="sa-table">
            <thead><tr><th>Site</th>{showTypes ? <th>Event type</th> : null}<th>Username</th><th>Password</th><th>Browser login</th><th /></tr></thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.site}>
                  <td className="sa-break">
                    <strong>{account.label}</strong>
                    <div className="sa-small"><a href={account.login_url} target="_blank" rel="noopener noreferrer">{account.login_url}</a></div>
                    {account.notes ? <div className="sa-small sa-muted">{account.notes}</div> : null}
                  </td>
                  {showTypes ? (
                    <td className="sa-nowrap">
                      {account.event_type
                        ? account.event_type_name || account.event_type
                        : <Badge tone="info" title="Every event type uses this login">All types</Badge>}
                    </td>
                  ) : null}
                  <td className="sa-break">{account.username || <span className="sa-muted">—</span>}</td>
                  <td>{account.has_password ? <Badge tone="success" title="Stored in the Mac's Keychain">Saved</Badge> : <Badge tone="neutral">None</Badge>}</td>
                  <td className="sa-nowrap">
                    {account.login_window_open ? (
                      <Badge tone="attention" title="Log in on the Mac, then close the tab">Tab open</Badge>
                    ) : account.has_profile ? (
                      <Badge tone="success">Saved</Badge>
                    ) : (
                      <Badge tone="warning" title="Open the login window and log in once">Not logged in yet</Badge>
                    )}
                    {account.last_login_ok_at ? <div className="sa-small sa-muted">Saved <AgentTime iso={account.last_login_ok_at} relative /></div> : null}
                  </td>
                  <td>
                    <div className="sa-row">
                      <Form method="post" className="sa-inline-form" preventScrollReset>
                        <Hidden values={{ intent: "login_window", site: account.site }} />
                        <button
                          type="submit"
                          className="sa-btn sa-btn-sm sa-btn-primary"
                          disabled={busy || account.login_window_open}
                          title={loginWindowHelp}
                        >
                          {isSubmitting("login_window", ["site", account.site]) ? "Opening…" : "Open login window"}
                        </button>
                      </Form>
                      <Link to={`?account=${encodeURIComponent(account.site)}#logins`} className="sa-btn sa-btn-sm" preventScrollReset>Edit</Link>
                      <Form
                        method="post"
                        className="sa-inline-form"
                        preventScrollReset
                        onSubmit={(event) => {
                          const forType = account.event_type ? ` for ${account.event_type_name || account.event_type}` : "";
                          if (!window.confirm(`Delete the ${account.label} login${forType}? Its saved username and password are removed.`)) event.preventDefault();
                        }}
                      >
                        <Hidden values={{ intent: "delete_account", site: account.site }} />
                        <button type="submit" className="sa-btn sa-btn-sm sa-btn-critical" disabled={busy}>Delete</button>
                      </Form>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : !showEditor ? (
        <EmptyState title="No site logins yet">
          Add the sites the agent should use, like Eventbrite or Meetup. Then open the login window and log in once.
        </EmptyState>
      ) : null}
    </s-section>
  );
}

export default function AgentSettings() {
  const data = useLoaderData<typeof loader>();
  const result = useActionData<typeof action>() as SettingsActionResult | undefined;
  usePolling(
    data.accounts.some((account) => account.login_window_open)
      || Boolean(data.browser?.login_tabs?.length)
      || Boolean(data.telegram?.configured && !data.telegram.linked),
    4_000,
  );
  const unscoped = result && !result.section ? result : undefined;

  return (
    <>
      <ActionBanner result={unscoped} />
      {data.settings ? (
        <>
          <WorkerSection settings={data.settings} result={result} />
          <ModelSection settings={data.settings} result={result} />
        </>
      ) : (
        <s-section heading="Worker, model and limits"><LoadError error={data.settingsError} /></s-section>
      )}
      {data.telegram ? <TelegramSection telegram={data.telegram} result={result} /> : (
        <s-section heading="Telegram"><LoadError error={data.telegramError} /></s-section>
      )}
      {data.email ? <EmailSection email={data.email} result={result} /> : (
        <s-section heading="Email account and site logins"><LoadError error={data.accountsError} /></s-section>
      )}
      <EventTypesSection
        data={data.eventTypes}
        error={data.eventTypesError}
        editKey={data.editEventType}
        newType={data.newEventType}
        addLoginFor={data.addLoginFor}
        savedEventType={data.savedEventType}
        savedTypeLogin={data.savedTypeLogin}
        result={result}
      />
      {data.email ? (
        <LoginsSection
          accounts={data.accounts}
          eventTypes={data.eventTypes?.event_types ?? null}
          browser={data.browser}
          editSite={data.editSite}
          newAccount={data.newAccount}
          savedAccount={data.savedAccount}
          result={result}
        />
      ) : null}
      {!data.settings && !data.email && !data.settingsError && !data.accountsError ? (
        <s-section><EmptyState title="Nothing to show" /></s-section>
      ) : null}
    </>
  );
}
