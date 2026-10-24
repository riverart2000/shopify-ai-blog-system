import type { LoaderFunctionArgs } from "react-router";

import { proxyMedia, seg, verifyTemplatePreviewSignature } from "../lib/social-engine.server";

/** Signed proxy for template preview renders (sample products in the store's brand kit). */
export const loader = async ({ request, params }: LoaderFunctionArgs) => {
  const key = params.key || "";
  const url = new URL(request.url);
  const format = url.searchParams.get("format") || "square";
  if (!verifyTemplatePreviewSignature(key, format, url)) {
    return new Response("Forbidden", { status: 403 });
  }
  return proxyMedia(`/templates/${seg(key)}/preview`, request, { format });
};
