import type { LoaderFunctionArgs } from "react-router";

import { authenticate } from "../shopify.server";

/** The old Publer-based Social Posts page now lives in Social Autopilot's studio. */
export const loader = async ({ request }: LoaderFunctionArgs) => {
  const { redirect } = await authenticate.admin(request);
  // Shopify's redirect helper carries over the request's query params (embedded params included).
  return redirect("/app/social/studio");
};
