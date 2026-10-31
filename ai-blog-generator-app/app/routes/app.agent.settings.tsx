import { useState } from "react";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "react-router";
import { Form, Link, useActionData, useLoaderData } from "react-router";

import { AgentTime, LoadError } from "../components/agent/parts";
import { ActionBanner, Badge, Banner, Card, EmptyState, Field, Hidden, Toggle, useBusy, usePolling } from "../components/social/ui";
import {
  TaskAgentError, errorMessage, formBool, formInt, formString, runAgentIntent, taFetch,
  type Account, type AccountSaveResponse, type AccountsResponse, type AgentActionResult, type BrowserResponse,
  type BrowserStatus, type EmailAccount, type EmailSaveResponse, type EmailTestResponse, type MessageResponse,
  type SettingsResponse,
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
  const [settingsResult, accountsResult, telegramResult] = await Promise.allSettled([
    taFetch<SettingsResponse>("/settings"),
    taFetch<AccountsResponse>("/accounts"),
    taFetch<TelegramResponse>("/telegram"),
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
    editSite,
    newAccount,
    savedAccount,
  };
};

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

  if (intent === "save_account") {
    let savedSite = "";
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
        },
      });
      savedSite = saved.account.site;
      return { message: saved.message };
    });
    if (result.ok && savedSite) return redirect(`/app/agent/settings?saved_account=${encodeURIComponent(savedSite)}#logins`);
    return { ...result, section: "logins" };
  }

  if (intent === "delete_account") {
    const site = formString(form, "site");
    const result = await runAgentIntent(intent, async () => {
      const deleted = await taFetch<MessageResponse>(`/accounts/${seg(site)}/delete`, { method: "POST" });
      return { message: deleted.message };
    }, site);
    return { ...result, section: "logins" };
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
      return { message: opened.message || "The shared browser is open on the Mac." };
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

function AccountEditor({ account }: { account: Account | null }) {
  const { busy, isSubmitting } = useBusy();
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

function BrowserState({ browser }: { browser: BrowserStatus }) {
  const loginTabs = browser.login_tabs || [];
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
  return (
    <Card muted>
      <div className="sa-stack-sm">
        <strong>Shared browser</strong>
        <p className="sa-small">
          All agents share one browser. Log in to a site once (here or when a task asks you on Telegram) and every task can use it.
        </p>
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
          <button type="submit" className="sa-btn" disabled={busy} title="Shows the shared browser on the Mac so you can log in to anything">
            {isSubmitting("open_browser") ? "Opening…" : "Open shared browser"}
          </button>
        </Form>
      </div>
    </Card>
  );
}

function LoginsSection({ accounts, browser, editSite, newAccount, savedAccount, result }: {
  accounts: Account[]; browser: BrowserStatus | null; editSite: string; newAccount: boolean; savedAccount: string;
  result?: SettingsActionResult;
}) {
  const { busy, isSubmitting } = useBusy();
  const editing = editSite ? accounts.find((account) => account.site === editSite) || null : null;
  const showEditor = newAccount || Boolean(editing);

  return (
    <s-section heading="Site logins">
      <div id="logins" />
      <SectionResult result={result} section="logins" />
      {savedAccount && !result ? (
        <Banner tone="success">
          Saved the {accounts.find((account) => account.site === savedAccount)?.label || savedAccount} login.
          {accounts.find((account) => account.site === savedAccount)?.has_profile ? "" : " Next: click “Open login window” and log in once in the shared browser on the Mac."}
        </Banner>
      ) : null}
      <div className="sa-spread" style={{ marginBottom: 10 }}>
        <p className="sa-muted" style={{ maxWidth: 640 }}>
          Accounts the agent can use, like a member of staff would. &ldquo;Open login window&rdquo; opens that site&apos;s login page as a
          tab in the shared browser on the Mac. Log in by hand, including any 2-step code, then close the tab.
        </p>
        {!showEditor ? <Link to="?new_account=1#logins" className="sa-btn sa-btn-primary" preventScrollReset>Add a site login</Link> : null}
      </div>
      <div style={{ marginBottom: 12 }}><SharedBrowserPanel browser={browser} /></div>
      {showEditor ? <div style={{ marginBottom: 12 }}><AccountEditor account={editing} /></div> : null}
      {accounts.length ? (
        <div className="sa-table-wrap">
          <table className="sa-table">
            <thead><tr><th>Site</th><th>Username</th><th>Password</th><th>Browser login</th><th /></tr></thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.site}>
                  <td className="sa-break">
                    <strong>{account.label}</strong>
                    <div className="sa-small"><a href={account.login_url} target="_blank" rel="noopener noreferrer">{account.login_url}</a></div>
                    {account.notes ? <div className="sa-small sa-muted">{account.notes}</div> : null}
                  </td>
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
                          title="Opens this site's login page as a tab in the shared browser on the Mac. Log in by hand, including any 2-step code, then close the tab."
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
                          if (!window.confirm(`Delete the ${account.label} login? Its saved username and password are removed.`)) event.preventDefault();
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
      {data.email ? (
        <LoginsSection
          accounts={data.accounts}
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
