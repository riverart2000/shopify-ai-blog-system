import type { LinksFunction, LoaderFunctionArgs, ShouldRevalidateFunction } from "react-router";
import { NavLink, Outlet, useLoaderData, useMatches } from "react-router";

import socialStyles from "../components/social/social.css?url";
import type { SocialLayoutData } from "../components/social/context";
import { Badge, Banner } from "../components/social/ui";
import { errorMessage, seFetch, type SettingsResponse } from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const links: LinksFunction = () => [{ rel: "stylesheet", href: socialStyles }];

export const loader = async ({ request }: LoaderFunctionArgs): Promise<SocialLayoutData> => {
  await authenticate.admin(request);
  try {
    return { settingsResponse: await seFetch<SettingsResponse>("/settings"), error: "" };
  } catch (error) {
    return { settingsResponse: null, error: errorMessage(error, "Social Autopilot settings could not be loaded.") };
  }
};

/**
 * Settings rarely change: reload them after a save on a page that edits them, or when
 * moving to another section — never on polling or filter changes within a page.
 */
export const shouldRevalidate: ShouldRevalidateFunction = ({ formMethod, formAction, currentUrl, nextUrl }) => {
  if (formMethod && formMethod.toUpperCase() !== "GET") {
    const path = (formAction || "").split("?")[0].replace(/\/+$/, "");
    return ["/app/social", "/app/social/settings", "/app/social/templates", "/app/social/channels"].includes(path);
  }
  return currentUrl.pathname !== nextUrl.pathname;
};

const SECTIONS: Array<{ to: string; label: string; end?: boolean }> = [
  { to: "/app/social", label: "Overview", end: true },
  { to: "/app/social/queue", label: "Approval queue" },
  { to: "/app/social/promotions", label: "Promotions" },
  { to: "/app/social/calendar", label: "Calendar" },
  { to: "/app/social/studio", label: "Create post" },
  { to: "/app/social/campaigns", label: "Campaigns" },
  { to: "/app/social/products", label: "Products" },
  { to: "/app/social/channels", label: "Channels" },
  { to: "/app/social/templates", label: "Templates & brand" },
  { to: "/app/social/analytics", label: "Analytics" },
  { to: "/app/social/activity", label: "Activity" },
  { to: "/app/social/settings", label: "Settings" },
];

export const handle = { title: "Social Autopilot" };

export default function SocialLayout() {
  const { settingsResponse, error } = useLoaderData<typeof loader>();
  const matches = useMatches();
  const title = [...matches].reverse()
    .map((match) => (match.handle as { title?: string } | undefined)?.title)
    .find(Boolean) || "Social Autopilot";
  const settings = settingsResponse?.settings;

  return (
    <s-page heading={title === "Social Autopilot" ? title : `Social Autopilot · ${title}`} inlineSize="large">
      <div className="sa sa-stack">
        <nav className="sa-subnav" aria-label="Social Autopilot sections">
          {SECTIONS.map((section) => (
            <NavLink key={section.to} to={section.to} end={section.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {section.label}
            </NavLink>
          ))}
        </nav>
        {settings ? (
          <div className="sa-row sa-small">
            <Badge tone={settings.enabled ? "success" : "neutral"}>{settings.enabled ? "Autopilot on" : "Autopilot off"}</Badge>
            {settings.publisher === "dry_run"
              ? <Badge tone="warning" title="Posts are prepared but never sent to Postiz">Practice mode (dry run)</Badge>
              : <Badge tone="info">Publishing through Postiz</Badge>}
            <span className="sa-muted">Store time zone: {settings.schedule.timezone}</span>
          </div>
        ) : null}
        {error ? <Banner tone="critical" title="Social Autopilot backend unavailable">{error}</Banner> : null}
        <Outlet />
      </div>
    </s-page>
  );
}
