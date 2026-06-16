import type { HeadersFunction, LoaderFunctionArgs } from "react-router";
import { Outlet, useLoaderData, useRouteError } from "react-router";
import { boundary } from "@shopify/shopify-app-react-router/server";
import { AppProvider } from "@shopify/shopify-app-react-router/react";

import { authenticate } from "../shopify.server";

export const loader = async ({ request }: LoaderFunctionArgs) => {
  await authenticate.admin(request);

  // eslint-disable-next-line no-undef
  return { apiKey: process.env.SHOPIFY_API_KEY || "" };
};

export default function App() {
  const { apiKey } = useLoaderData<typeof loader>();

  return (
    <AppProvider embedded apiKey={apiKey}>
      <s-app-nav>
        <s-link href="/app">Blog Studio</s-link>
        <s-link href="/app/product-blogs">Product Blogs</s-link>
        <s-link href="/app/social-posts">Social Posts</s-link>
        <s-link href="/app/history">History</s-link>
        <s-link href="/app/schedule">Schedule</s-link>
        <s-link href="/app/prompts">Prompts</s-link>
        <s-link href="/app/models">Models</s-link>
        <s-link href="/app/settings">Settings</s-link>
        <s-link href="/app/additional">Setup &amp; Limits</s-link>
      </s-app-nav>
      <Outlet />
    </AppProvider>
  );
}

// Shopify needs React Router to catch some thrown responses, so that their headers are included in the response.
export function ErrorBoundary() {
  return boundary.error(useRouteError());
}

export const headers: HeadersFunction = (headersArgs) => {
  return boundary.headers(headersArgs);
};
