import type { LoaderFunctionArgs } from "react-router";
import { authenticate } from "../shopify.server";
import { reviewsBackendFetch, reviewsBackendConfigured } from "../lib/reviews.server";

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const context = await authenticate.admin(request);
  if (!reviewsBackendConfigured()) return new Response("Reviews backend is not configured.", { status: 503 });
  const response = await reviewsBackendFetch(`/api/reviews/export.csv?shop=${encodeURIComponent(context.session.shop)}`);
  return new Response(await response.text(), {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition": "attachment; filename=bioluxelab-reviews.csv",
    },
  });
};
