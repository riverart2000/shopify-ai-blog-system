import type { ActionFunctionArgs, LinksFunction, LoaderFunctionArgs, ShouldRevalidateFunction } from "react-router";
import { NavLink, Outlet, redirect, useLoaderData, useMatches, useSubmit } from "react-router";

import socialStyles from "../components/social/social.css?url";
import type { SocialLayoutData } from "../components/social/context";
import { Badge, Banner } from "../components/social/ui";
import { errorMessage, seFetch, type BrandsResponse, type SettingsResponse } from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const links: LinksFunction = () => [{ rel: "stylesheet", href: socialStyles }];

export const loader = async ({ request }: LoaderFunctionArgs): Promise<SocialLayoutData> => {
  await authenticate.admin(request);
  try {
    const [settingsResponse, brands] = await Promise.all([
      seFetch<SettingsResponse>("/settings"),
      seFetch<BrandsResponse>("/brands").catch(() => ({ active: "", brands: [] }) as BrandsResponse),
    ]);
    return { settingsResponse, brands: brands.brands, error: "" };
  } catch (error) {
    return { settingsResponse: null, brands: [], error: errorMessage(error, "Social Autopilot settings could not be loaded.") };
  }
};

/** The brand switch: every Social Autopilot page then shows the chosen brand. */
export const action = async ({ request }: ActionFunctionArgs) => {
  await authenticate.admin(request);
  const form = await request.formData();
  const storeId = String(form.get("brand") || "");
  if (storeId) await seFetch("/brands/active", { body: { store_id: storeId } });
  return redirect("/app/social");
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

/** `catalog`: the section is about Shopify products, so it is hidden for a brand that has no catalogue. */
const SECTIONS: Array<{ to: string; label: string; end?: boolean; catalog?: boolean }> = [
  { to: "/app/social", label: "Overview", end: true },
  { to: "/app/social/queue", label: "Approval queue" },
  { to: "/app/social/promotions", label: "Promotions", catalog: true },
  { to: "/app/social/calendar", label: "Calendar" },
  { to: "/app/social/studio", label: "Create post", catalog: true },
  { to: "/app/social/founder-videos", label: "Founder videos", catalog: true },
  { to: "/app/social/campaigns", label: "Campaigns" },
  { to: "/app/social/products", label: "Products", catalog: true },
  { to: "/app/social/listings", label: "Listings", catalog: true },
  { to: "/app/social/channels", label: "Channels" },
  { to: "/app/social/templates", label: "Templates & brand" },
  { to: "/app/social/analytics", label: "Analytics" },
  { to: "/app/social/activity", label: "Activity" },
  { to: "/app/social/settings", label: "Settings" },
];

export const handle = { title: "Social Autopilot" };

export default function SocialLayout() {
  const { settingsResponse, brands, error } = useLoaderData<typeof loader>();
  const submit = useSubmit();
  const matches = useMatches();
  const title = [...matches].reverse()
    .map((match) => (match.handle as { title?: string } | undefined)?.title)
    .find(Boolean) || "Social Autopilot";
  const settings = settingsResponse?.settings;
  const hasCatalog = settings?.catalog?.source !== "none";
  const activeBrand = brands.find((brand) => brand.active);

  return (
    <s-page heading={title === "Social Autopilot" ? title : `Social Autopilot · ${title}`} inlineSize="large">
      <div className="sa sa-stack">
        <nav className="sa-subnav" aria-label="Social Autopilot sections">
          {SECTIONS.filter((section) => hasCatalog || !section.catalog).map((section) => (
            <NavLink key={section.to} to={section.to} end={section.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {section.label}
            </NavLink>
          ))}
        </nav>
        {settings ? (
          <div className="sa-row sa-small">
            {brands.length > 1 ? (
              <label className="sa-row sa-small" title="Each brand has its own channels, campaigns, posts and settings">
                <strong>Brand</strong>
                <select
                  value={activeBrand?.store_id || ""}
                  onChange={(event) => submit({ brand: event.target.value }, { method: "post", action: "/app/social" })}
                >
                  {brands.map((brand) => (
                    <option key={brand.store_id} value={brand.store_id}>{brand.name}{brand.enabled ? "" : " (off)"}</option>
                  ))}
                </select>
              </label>
            ) : null}
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
