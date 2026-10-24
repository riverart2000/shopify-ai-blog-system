import type { LoaderFunctionArgs } from "react-router";

import { proxyMedia, seg, verifyMediaSignature } from "../lib/social-engine.server";

/**
 * Signed proxy for rendered post media (images and videos, with Range support).
 * <img>/<video> requests from the admin iframe carry no session token, so access is
 * granted by the HMAC signature minted by the page loader instead.
 */
export const loader = async ({ request, params }: LoaderFunctionArgs) => {
  const assetId = params.assetId || "";
  const url = new URL(request.url);
  if (!verifyMediaSignature(assetId, url)) {
    return new Response("Forbidden", { status: 403 });
  }
  return proxyMedia(`/media/${seg(assetId)}`, request);
};
