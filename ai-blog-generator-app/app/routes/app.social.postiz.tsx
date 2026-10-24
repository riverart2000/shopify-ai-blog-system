import type { LoaderFunctionArgs, ShouldRevalidateFunction } from "react-router";
import { Link, useLoaderData } from "react-router";

import { PostizToolsPanel } from "../components/social/PostizParts";
import { LoadError } from "../components/social/ui";
import {
  errorMessage, seFetch, type PostizMediaResponse, type PostizToolsResponse,
} from "../lib/social-engine.server";
import { authenticate } from "../shopify.server";

export const handle = { title: "Postiz tools" };

/**
 * Postiz creative tools page. Its loader also serves the in-page pickers (via useFetcher):
 *   ?view=media&search=…&page=…  → Postiz media library (designs)
 *   (default)                     → GET /postiz/tools (live flag, credits, generators, voices, how-tos)
 * Postiz calls are made only when asked for — never on background polling.
 */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);
  const params = new URL(request.url).searchParams;
  if (params.get("view") === "media") {
    try {
      const media = await seFetch<PostizMediaResponse>("/postiz/media", {
        query: { search: params.get("search") || "", page: Math.max(1, Number(params.get("page") || 1) || 1) },
        timeoutMs: 60_000,
      });
      return { view: "media" as const, media, tools: null, error: "" };
    } catch (error) {
      return { view: "media" as const, media: null, tools: null, error: errorMessage(error) };
    }
  }
  try {
    const tools = await seFetch<PostizToolsResponse>("/postiz/tools", { timeoutMs: 60_000 });
    return { view: "tools" as const, media: null, tools, error: "" };
  } catch (error) {
    return { view: "tools" as const, media: null, tools: null, error: errorMessage(error) };
  }
};

/** Never re-run after other pages' actions or polling (each run calls Postiz). */
export const shouldRevalidate: ShouldRevalidateFunction = () => false;

export default function PostizToolsPage() {
  const data = useLoaderData<typeof loader>();
  return (
    <>
      <div className="sa-row"><Link to="/app/social/channels">← Channels</Link></div>
      <LoadError error={data.tools ? "" : data.error} />
      <s-section heading="Postiz tools">
        <p className="sa-muted" style={{ marginBottom: 10 }}>
          Social Autopilot publishes through Postiz and uses Postiz&apos;s own creative tools wherever it can. Credits reset monthly; limits are set in{" "}
          <Link to="/app/social/settings#postiz">Settings → Postiz</Link>.
        </p>
        <PostizToolsPanel tools={data.tools} error={data.error} />
      </s-section>
    </>
  );
}
