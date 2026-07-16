#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import dotenv from "dotenv";
import { GraphQLClient, gql } from "graphql-request";
import minimist from "minimist";
import { z } from "zod";

import { getCustomerOrders } from "shopify-mcp/dist/tools/getCustomerOrders.js";
import { getCustomers } from "shopify-mcp/dist/tools/getCustomers.js";
import { getOrderById } from "shopify-mcp/dist/tools/getOrderById.js";
import { getOrders } from "shopify-mcp/dist/tools/getOrders.js";
import { getProductById } from "shopify-mcp/dist/tools/getProductById.js";
import { getProducts } from "shopify-mcp/dist/tools/getProducts.js";
import { updateCustomer } from "shopify-mcp/dist/tools/updateCustomer.js";
import { updateOrder } from "shopify-mcp/dist/tools/updateOrder.js";
import { createProduct } from "shopify-mcp/dist/tools/createProduct.js";
import { updateProduct } from "shopify-mcp/dist/tools/updateProduct.js";
import { manageProductVariants } from "shopify-mcp/dist/tools/manageProductVariants.js";
import { deleteProductVariants } from "shopify-mcp/dist/tools/deleteProductVariants.js";
import { deleteProduct } from "shopify-mcp/dist/tools/deleteProduct.js";
import { manageProductOptions } from "shopify-mcp/dist/tools/manageProductOptions.js";
import { ShopifyAuth } from "shopify-mcp/dist/lib/shopifyAuth.js";

dotenv.config();

const argv = minimist(process.argv.slice(2));
const SHOPIFY_ACCESS_TOKEN = argv.accessToken || process.env.SHOPIFY_ACCESS_TOKEN;
const SHOPIFY_CLIENT_ID = argv.clientId || process.env.SHOPIFY_CLIENT_ID;
const SHOPIFY_CLIENT_SECRET =
  argv.clientSecret || process.env.SHOPIFY_CLIENT_SECRET;
const MYSHOPIFY_DOMAIN = argv.domain || process.env.MYSHOPIFY_DOMAIN;
const API_VERSION = argv.apiVersion || process.env.SHOPIFY_API_VERSION || "2026-01";
const CJ_API_KEY = argv.cjApiKey || process.env.CJ_API_KEY;
const CJ_API_BASE_URL =
  argv.cjApiBaseUrl ||
  process.env.CJ_API_BASE_URL ||
  "https://developers.cjdropshipping.com/api2.0/v1";
const CJ_API_DOCS =
  argv.cjApiDocs ||
  process.env.CJ_API_DOCS ||
  process.env.CJ_API_DOCS_URL ||
  "https://developers.cjdropshipping.com/en/api/introduction.html";
const useClientCredentials = !!(SHOPIFY_CLIENT_ID && SHOPIFY_CLIENT_SECRET);

process.env.MYSHOPIFY_DOMAIN = MYSHOPIFY_DOMAIN;

if (!SHOPIFY_ACCESS_TOKEN && !useClientCredentials) {
  console.error("Error: Authentication credentials are required.");
  console.error("Provide SHOPIFY_ACCESS_TOKEN or SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET.");
  process.exit(1);
}

if (!MYSHOPIFY_DOMAIN) {
  console.error("Error: MYSHOPIFY_DOMAIN is required.");
  process.exit(1);
}

let accessToken;
let auth = null;

if (useClientCredentials) {
  auth = new ShopifyAuth({
    clientId: SHOPIFY_CLIENT_ID,
    clientSecret: SHOPIFY_CLIENT_SECRET,
    shopDomain: MYSHOPIFY_DOMAIN,
  });
  accessToken = await auth.initialize();
} else {
  accessToken = SHOPIFY_ACCESS_TOKEN;
}

process.env.SHOPIFY_ACCESS_TOKEN = accessToken;

const shopifyClient = new GraphQLClient(
  `https://${MYSHOPIFY_DOMAIN}/admin/api/${API_VERSION}/graphql.json`,
  {
    headers: {
      "X-Shopify-Access-Token": accessToken,
      "Content-Type": "application/json",
    },
  },
);

if (auth) {
  auth.setGraphQLClient(shopifyClient);
}

[
  getProducts,
  getProductById,
  getCustomers,
  getOrders,
  getOrderById,
  updateOrder,
  getCustomerOrders,
  updateCustomer,
  createProduct,
  updateProduct,
  manageProductVariants,
  deleteProductVariants,
  deleteProduct,
  manageProductOptions,
].forEach((tool) => tool.initialize(shopifyClient));

const server = new McpServer({
  name: "shopify",
  version: "2.0.0",
  description:
    "Extended Shopify MCP for store operations via GraphQL Admin API",
});

function asTextContent(result) {
  return {
    content: [{ type: "text", text: JSON.stringify(result) }],
  };
}

const freeformObjectSchema = z.record(z.string(), z.any());
const cjWebhookTopicSchema = z.object({
  type: z.enum(["ENABLE", "CANCEL"]),
  callbackUrls: z.array(z.string().url()).length(1),
});

async function request(query, variables = {}) {
  return shopifyClient.request(query, variables);
}

function storefrontClient(storefrontAccessToken) {
  const token =
    storefrontAccessToken ||
    process.env.SHOPIFY_STOREFRONT_ACCESS_TOKEN ||
    process.env.SHOPIFY_STOREFRONT_PRIVATE_TOKEN;

  if (!token) {
    throw new Error(
      "A Storefront API token is required. Set SHOPIFY_STOREFRONT_ACCESS_TOKEN or SHOPIFY_STOREFRONT_PRIVATE_TOKEN, or pass storefrontAccessToken.",
    );
  }

  const headers = {
    "Content-Type": "application/json",
  };

  if (process.env.SHOPIFY_STOREFRONT_PRIVATE_TOKEN && token === process.env.SHOPIFY_STOREFRONT_PRIVATE_TOKEN) {
    headers["Shopify-Storefront-Private-Token"] = token;
  } else {
    headers["X-Shopify-Storefront-Access-Token"] = token;
  }

  return new GraphQLClient(`https://${MYSHOPIFY_DOMAIN}/api/${API_VERSION}/graphql.json`, {
    headers,
  });
}

async function storefrontRequest(query, variables = {}, storefrontAccessToken) {
  return storefrontClient(storefrontAccessToken).request(query, variables);
}

const cjSession = {
  openId: null,
  accessToken: null,
  accessTokenExpiryDate: null,
  refreshToken: null,
  refreshTokenExpiryDate: null,
  createDate: null,
};

function isExpiringSoon(isoDateString) {
  if (!isoDateString) {
    return true;
  }

  const expiry = new Date(isoDateString).getTime();
  if (Number.isNaN(expiry)) {
    return true;
  }

  return expiry - Date.now() < 60_000;
}

function withQueryParams(url, query = {}) {
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") {
      return;
    }

    if (Array.isArray(value)) {
      value.forEach((item) => {
        if (item !== undefined && item !== null && item !== "") {
          url.searchParams.append(key, String(item));
        }
      });
      return;
    }

    url.searchParams.set(key, String(value));
  });
}

async function cjAuthRequest(path, body) {
  const response = await fetch(new URL(path, `${CJ_API_BASE_URL}/`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  const text = await response.text();
  let data;

  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`CJ auth request failed (${response.status}): ${text}`);
  }

  if (!response.ok || data.code !== 200 || data.result === false) {
    throw new Error(
      `CJ auth request failed (${response.status}): ${data.message || text}`,
    );
  }

  return data;
}

async function ensureCjSession(forceRefresh = false) {
  if (!CJ_API_KEY) {
    throw new Error("CJ_API_KEY is required to use CJ tools.");
  }

  if (!forceRefresh && cjSession.accessToken && !isExpiringSoon(cjSession.accessTokenExpiryDate)) {
    return cjSession;
  }

  let response;
  if (
    !forceRefresh &&
    cjSession.refreshToken &&
    !isExpiringSoon(cjSession.refreshTokenExpiryDate)
  ) {
    response = await cjAuthRequest("authentication/refreshAccessToken", {
      refreshToken: cjSession.refreshToken,
    });
  } else {
    response = await cjAuthRequest("authentication/getAccessToken", {
      apiKey: CJ_API_KEY,
    });
  }

  Object.assign(cjSession, response.data || {});
  return cjSession;
}

async function cjRequest({
  path,
  method = "GET",
  query = {},
  body,
  headers = {},
  authenticated = true,
}) {
  const url = new URL(path, `${CJ_API_BASE_URL}/`);
  withQueryParams(url, query);

  const requestHeaders = {
    ...headers,
  };

  if (authenticated) {
    const session = await ensureCjSession();
    requestHeaders["CJ-Access-Token"] = session.accessToken;
  }

  if (body !== undefined) {
    requestHeaders["Content-Type"] = "application/json";
  }

  const response = await fetch(url, {
    method,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  const text = await response.text();
  let data;

  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`CJ request failed (${response.status}): ${text}`);
  }

  if (!response.ok || (data.code !== undefined && data.code !== 200) || data.result === false) {
    throw new Error(`CJ request failed (${response.status}): ${data.message || text}`);
  }

  return data;
}

server.tool(
  "get-products",
  { searchTitle: z.string().optional(), limit: z.number().default(10) },
  async (args) => asTextContent(await getProducts.execute(args)),
);

server.tool(
  "get-product-by-id",
  { productId: z.string().min(1) },
  async (args) => asTextContent(await getProductById.execute(args)),
);

server.tool(
  "get-customers",
  { searchQuery: z.string().optional(), limit: z.number().default(10) },
  async (args) => asTextContent(await getCustomers.execute(args)),
);

server.tool(
  "get-orders",
  {
    status: z.enum(["any", "open", "closed", "cancelled"]).default("any"),
    limit: z.number().default(10),
  },
  async (args) => asTextContent(await getOrders.execute(args)),
);

server.tool(
  "get-order-by-id",
  { orderId: z.string().min(1) },
  async (args) => asTextContent(await getOrderById.execute(args)),
);

server.tool(
  "update-order",
  {
    id: z.string().min(1),
    tags: z.array(z.string()).optional(),
    email: z.string().email().optional(),
    note: z.string().optional(),
    customAttributes: z
      .array(z.object({ key: z.string(), value: z.string() }))
      .optional(),
    metafields: z
      .array(
        z.object({
          id: z.string().optional(),
          namespace: z.string().optional(),
          key: z.string().optional(),
          value: z.string(),
          type: z.string().optional(),
        }),
      )
      .optional(),
    shippingAddress: z
      .object({
        address1: z.string().optional(),
        address2: z.string().optional(),
        city: z.string().optional(),
        company: z.string().optional(),
        country: z.string().optional(),
        firstName: z.string().optional(),
        lastName: z.string().optional(),
        phone: z.string().optional(),
        province: z.string().optional(),
        zip: z.string().optional(),
      })
      .optional(),
  },
  async (args) => asTextContent(await updateOrder.execute(args)),
);

server.tool(
  "get-customer-orders",
  {
    customerId: z
      .string()
      .regex(/^\d+$/, "Customer ID must be numeric"),
    limit: z.number().default(10),
  },
  async (args) => asTextContent(await getCustomerOrders.execute(args)),
);

server.tool(
  "update-customer",
  {
    id: z.string().regex(/^\d+$/, "Customer ID must be numeric"),
    firstName: z.string().optional(),
    lastName: z.string().optional(),
    email: z.string().email().optional(),
    phone: z.string().optional(),
    tags: z.array(z.string()).optional(),
    note: z.string().optional(),
    taxExempt: z.boolean().optional(),
    metafields: z
      .array(
        z.object({
          id: z.string().optional(),
          namespace: z.string().optional(),
          key: z.string().optional(),
          value: z.string(),
          type: z.string().optional(),
        }),
      )
      .optional(),
  },
  async (args) => asTextContent(await updateCustomer.execute(args)),
);

server.tool(
  "create-product",
  {
    title: z.string().min(1),
    descriptionHtml: z.string().optional(),
    handle: z.string().optional(),
    vendor: z.string().optional(),
    productType: z.string().optional(),
    tags: z.array(z.string()).optional(),
    status: z.enum(["ACTIVE", "DRAFT", "ARCHIVED"]).default("DRAFT"),
    seo: z
      .object({
        title: z.string().optional(),
        description: z.string().optional(),
      })
      .optional(),
    metafields: z
      .array(
        z.object({
          namespace: z.string(),
          key: z.string(),
          value: z.string(),
          type: z.string(),
        }),
      )
      .optional(),
    productOptions: z
      .array(
        z.object({
          name: z.string(),
          values: z.array(z.object({ name: z.string() })).optional(),
        }),
      )
      .optional(),
    collectionsToJoin: z.array(z.string()).optional(),
  },
  async (args) => asTextContent(await createProduct.execute(args)),
);

server.tool(
  "update-product",
  {
    id: z.string().min(1),
    title: z.string().optional(),
    descriptionHtml: z.string().optional(),
    handle: z.string().optional(),
    vendor: z.string().optional(),
    productType: z.string().optional(),
    tags: z.array(z.string()).optional(),
    status: z.enum(["ACTIVE", "DRAFT", "ARCHIVED"]).optional(),
    seo: z
      .object({
        title: z.string().optional(),
        description: z.string().optional(),
      })
      .optional(),
    metafields: z
      .array(
        z.object({
          id: z.string().optional(),
          namespace: z.string().optional(),
          key: z.string().optional(),
          value: z.string(),
          type: z.string().optional(),
        }),
      )
      .optional(),
    collectionsToJoin: z.array(z.string()).optional(),
    collectionsToLeave: z.array(z.string()).optional(),
    redirectNewHandle: z.boolean().optional(),
  },
  async (args) => asTextContent(await updateProduct.execute(args)),
);

server.tool(
  "manage-product-variants",
  {
    productId: z.string().min(1),
    variants: z
      .array(
        z.object({
          id: z.string().optional(),
          price: z.string().optional(),
          compareAtPrice: z.string().optional(),
          sku: z.string().optional(),
          tracked: z.boolean().optional(),
          taxable: z.boolean().optional(),
          barcode: z.string().optional(),
          optionValues: z
            .array(z.object({ optionName: z.string(), name: z.string() }))
            .optional(),
        }),
      )
      .min(1),
    strategy: z
      .enum(["DEFAULT", "REMOVE_STANDALONE_VARIANT", "PRESERVE_STANDALONE_VARIANT"])
      .optional(),
  },
  async (args) => asTextContent(await manageProductVariants.execute(args)),
);

server.tool(
  "manage-product-options",
  {
    productId: z.string().min(1),
    action: z.enum(["create", "update", "delete"]),
    options: z
      .array(
        z.object({
          name: z.string(),
          position: z.number().optional(),
          values: z.array(z.string()).optional(),
        }),
      )
      .optional(),
    optionId: z.string().optional(),
    name: z.string().optional(),
    position: z.number().optional(),
    valuesToAdd: z.array(z.string()).optional(),
    valuesToDelete: z.array(z.string()).optional(),
    optionIds: z.array(z.string()).optional(),
  },
  async (args) => asTextContent(await manageProductOptions.execute(args)),
);

server.tool(
  "delete-product",
  { id: z.string().min(1) },
  async (args) => asTextContent(await deleteProduct.execute(args)),
);

server.tool(
  "delete-product-variants",
  {
    productId: z.string().min(1),
    variantIds: z.array(z.string().min(1)).min(1),
  },
  async (args) => asTextContent(await deleteProductVariants.execute(args)),
);

server.tool(
  "admin-graphql",
  {
    query: z.string().min(1).describe("GraphQL query or mutation string"),
    variables: z.record(z.string(), z.any()).optional(),
  },
  async ({ query, variables }) => {
    const result = await request(query, variables || {});
    return asTextContent(result);
  },
);

server.tool(
  "introspect-type",
  {
    typeName: z.string().min(1).describe("GraphQL type name to introspect"),
  },
  async ({ typeName }) => {
    const query = gql`
      query IntrospectType($typeName: String!) {
        __type(name: $typeName) {
          kind
          name
          fields {
            name
          }
          inputFields {
            name
          }
          enumValues {
            name
          }
        }
      }
    `;
    const result = await request(query, { typeName });
    return asTextContent(result);
  },
);

server.tool(
  "get-shop",
  {},
  async () => {
    const query = gql`
      query GetShop {
        shop {
          name
          myshopifyDomain
          primaryDomain {
            host
            url
          }
          currencyCode
        }
      }
    `;
    return asTextContent(await request(query));
  },
);

server.tool(
  "get-pages",
  {
    searchTitle: z.string().optional(),
    limit: z.number().default(10),
  },
  async ({ searchTitle, limit }) => {
    const query = gql`
      query GetPages($first: Int!, $query: String) {
        pages(first: $first, query: $query) {
          edges {
            node {
              id
              title
              handle
              body
              isPublished
              updatedAt
            }
          }
        }
      }
    `;
    const variables = {
      first: limit,
      query: searchTitle ? `title:*${searchTitle}*` : undefined,
    };
    return asTextContent(await request(query, variables));
  },
);

server.tool(
  "create-page",
  {
    title: z.string().min(1),
    handle: z.string().optional(),
    body: z.string().optional(),
    isPublished: z.boolean().optional(),
  },
  async ({ title, handle, body, isPublished }) => {
    const query = gql`
      mutation CreatePage($page: PageCreateInput!) {
        pageCreate(page: $page) {
          page {
            id
            title
            handle
            body
            isPublished
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(
      await request(query, { page: { title, handle, body, isPublished } }),
    );
  },
);

server.tool(
  "update-page",
  {
    id: z.string().min(1),
    title: z.string().optional(),
    handle: z.string().optional(),
    body: z.string().optional(),
    isPublished: z.boolean().optional(),
    redirectNewHandle: z.boolean().optional(),
  },
  async ({ id, ...page }) => {
    const query = gql`
      mutation UpdatePage($id: ID!, $page: PageUpdateInput!) {
        pageUpdate(id: $id, page: $page) {
          page {
            id
            title
            handle
            body
            isPublished
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { id, page }));
  },
);

server.tool(
  "delete-page",
  { id: z.string().min(1) },
  async ({ id }) => {
    const query = gql`
      mutation DeletePage($id: ID!) {
        pageDelete(id: $id) {
          deletedPageId
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { id }));
  },
);

server.tool(
  "get-blogs",
  { limit: z.number().default(10) },
  async ({ limit }) => {
    const query = gql`
      query GetBlogs($first: Int!) {
        blogs(first: $first) {
          edges {
            node {
              id
              title
              handle
            }
          }
        }
      }
    `;
    return asTextContent(await request(query, { first: limit }));
  },
);

server.tool(
  "get-articles",
  {
    blogId: z.string().optional(),
    limit: z.number().default(10),
  },
  async ({ blogId, limit }) => {
    const query = blogId
      ? gql`
          query GetBlogArticles($id: ID!, $first: Int!) {
            blog(id: $id) {
              id
              title
              articles(first: $first) {
                edges {
                  node {
                    id
                    title
                    handle
                    isPublished
                    publishedAt
                    summary
                  }
                }
              }
            }
          }
        `
      : gql`
          query GetArticles($first: Int!) {
            articles(first: $first) {
              edges {
                node {
                  id
                  title
                  handle
                  isPublished
                  publishedAt
                  summary
                  blog {
                    id
                    title
                  }
                }
              }
            }
          }
        `;
    return asTextContent(
      await request(query, blogId ? { id: blogId, first: limit } : { first: limit }),
    );
  },
);

const menuItemInputSchema = z.lazy(() =>
  z.object({
    id: z.string().optional(),
    title: z.string(),
    type: z.string(),
    resourceId: z.string().optional(),
    url: z.string().optional(),
    tags: z.array(z.string()).optional(),
    items: z.array(menuItemInputSchema).optional(),
  }),
);

server.tool(
  "get-menus",
  { limit: z.number().default(10) },
  async ({ limit }) => {
    const query = gql`
      query GetMenus($first: Int!) {
        menus(first: $first) {
          edges {
            node {
              id
              title
              handle
              isDefault
              items {
                id
                title
                type
                url
                resourceId
                items {
                  id
                  title
                  type
                  url
                  resourceId
                }
              }
            }
          }
        }
      }
    `;
    return asTextContent(await request(query, { first: limit }));
  },
);

server.tool(
  "update-menu",
  {
    id: z.string().min(1),
    title: z.string().min(1),
    handle: z.string().optional(),
    items: z.array(menuItemInputSchema),
  },
  async ({ id, title, handle, items }) => {
    const query = gql`
      mutation UpdateMenu(
        $id: ID!
        $title: String!
        $handle: String
        $items: [MenuItemUpdateInput!]!
      ) {
        menuUpdate(id: $id, title: $title, handle: $handle, items: $items) {
          menu {
            id
            title
            handle
            items {
              id
              title
              type
              url
            }
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { id, title, handle, items }));
  },
);

server.tool(
  "get-themes",
  { limit: z.number().default(10) },
  async ({ limit }) => {
    const query = gql`
      query GetThemes($first: Int!) {
        themes(first: $first) {
          edges {
            node {
              id
              name
              role
              processing
            }
          }
        }
      }
    `;
    return asTextContent(await request(query, { first: limit }));
  },
);

server.tool(
  "get-files",
  { limit: z.number().default(10) },
  async ({ limit }) => {
    const query = gql`
      query GetFiles($first: Int!) {
        files(first: $first) {
          edges {
            node {
              id
              alt
              createdAt
              fileStatus
              ... on MediaImage {
                image {
                  url
                }
              }
            }
          }
        }
      }
    `;
    return asTextContent(await request(query, { first: limit }));
  },
);

server.tool(
  "get-shop-policies",
  {},
  async () => {
    const query = gql`
      query GetShopPolicies {
        shop {
          shopPolicies {
            id
            title
            type
            url
            body
            createdAt
            updatedAt
          }
        }
      }
    `;
    return asTextContent(await request(query));
  },
);

server.tool(
  "update-shop-policy",
  {
    type: z.enum([
      "REFUND_POLICY",
      "SHIPPING_POLICY",
      "PRIVACY_POLICY",
      "TERMS_OF_SERVICE",
      "TERMS_OF_SALE",
      "LEGAL_NOTICE",
      "SUBSCRIPTION_POLICY",
      "CONTACT_INFORMATION",
    ]),
    title: z.string().optional(),
    body: z.string().min(1),
    url: z.string().optional(),
  },
  async ({ type, title, body, url }) => {
    const query = gql`
      mutation UpdateShopPolicy($shopPolicy: ShopPolicyInput!) {
        shopPolicyUpdate(shopPolicy: $shopPolicy) {
          shopPolicy {
            id
            title
            type
            url
            body
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { shopPolicy: { type, title, body, url } }));
  },
);

server.tool(
  "create-blog",
  {
    title: z.string().min(1),
    handle: z.string().optional(),
    commentPolicy: z.string().optional(),
    templateSuffix: z.string().optional(),
    metafields: z.array(freeformObjectSchema).optional(),
  },
  async ({ title, ...blog }) => {
    const query = gql`
      mutation CreateBlog($blog: BlogCreateInput!) {
        blogCreate(blog: $blog) {
          blog {
            id
            title
            handle
            commentPolicy
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { blog: { title, ...blog } }));
  },
);

server.tool(
  "update-blog",
  {
    id: z.string().min(1),
    title: z.string().optional(),
    handle: z.string().optional(),
    commentPolicy: z.string().optional(),
    templateSuffix: z.string().optional(),
    metafields: z.array(freeformObjectSchema).optional(),
    redirectNewHandle: z.boolean().optional(),
    redirectArticles: z.boolean().optional(),
  },
  async ({ id, ...blog }) => {
    const query = gql`
      mutation UpdateBlog($id: ID!, $blog: BlogUpdateInput!) {
        blogUpdate(id: $id, blog: $blog) {
          blog {
            id
            title
            handle
            commentPolicy
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { id, blog }));
  },
);

server.tool(
  "delete-blog",
  { id: z.string().min(1) },
  async ({ id }) => {
    const query = gql`
      mutation DeleteBlog($id: ID!) {
        blogDelete(id: $id) {
          deletedBlogId
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { id }));
  },
);

server.tool(
  "create-article",
  {
    title: z.string().min(1),
    blogId: z.string().min(1),
    handle: z.string().optional(),
    body: z.string().optional(),
    summary: z.string().optional(),
    isPublished: z.boolean().optional(),
    publishDate: z.string().optional(),
    templateSuffix: z.string().optional(),
    tags: z.array(z.string()).optional(),
    author: z
      .object({
        name: z.string().optional(),
        email: z.string().optional(),
      })
      .optional(),
    image: freeformObjectSchema.optional(),
    metafields: z.array(freeformObjectSchema).optional(),
  },
  async ({ title, ...article }) => {
    const query = gql`
      mutation CreateArticle($article: ArticleCreateInput!) {
        articleCreate(article: $article) {
          article {
            id
            title
            handle
            isPublished
            publishedAt
            summary
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { article: { title, ...article } }));
  },
);

server.tool(
  "update-article",
  {
    id: z.string().min(1),
    title: z.string().optional(),
    blogId: z.string().optional(),
    handle: z.string().optional(),
    body: z.string().optional(),
    summary: z.string().optional(),
    isPublished: z.boolean().optional(),
    publishDate: z.string().optional(),
    templateSuffix: z.string().optional(),
    tags: z.array(z.string()).optional(),
    author: freeformObjectSchema.optional(),
    image: freeformObjectSchema.optional(),
    metafields: z.array(freeformObjectSchema).optional(),
    redirectNewHandle: z.boolean().optional(),
  },
  async ({ id, ...article }) => {
    const query = gql`
      mutation UpdateArticle($id: ID!, $article: ArticleUpdateInput!) {
        articleUpdate(id: $id, article: $article) {
          article {
            id
            title
            handle
            isPublished
            publishedAt
            summary
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { id, article }));
  },
);

server.tool(
  "delete-article",
  { id: z.string().min(1) },
  async ({ id }) => {
    const query = gql`
      mutation DeleteArticle($id: ID!) {
        articleDelete(id: $id) {
          deletedArticleId
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { id }));
  },
);

server.tool(
  "get-marketing-activities",
  { limit: z.number().default(10) },
  async ({ limit }) => {
    const query = gql`
      query GetMarketingActivities($first: Int!) {
        marketingActivities(first: $first) {
          edges {
            node {
              id
              title
              status
              statusLabel
              tactic
              marketingChannelType
              isExternal
              createdAt
              updatedAt
              sourceAndMedium
              urlParameterValue
            }
          }
        }
      }
    `;
    return asTextContent(await request(query, { first: limit }));
  },
);

server.tool(
  "create-marketing-activity-external",
  { input: freeformObjectSchema },
  async ({ input }) => {
    const query = gql`
      mutation CreateMarketingActivityExternal(
        $input: MarketingActivityCreateExternalInput!
      ) {
        marketingActivityCreateExternal(input: $input) {
          marketingActivity {
            id
            title
            status
            statusLabel
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { input }));
  },
);

server.tool(
  "update-marketing-activity-external",
  {
    input: freeformObjectSchema,
    marketingActivityId: z.string().optional(),
    remoteId: z.string().optional(),
    utm: freeformObjectSchema.optional(),
  },
  async ({ input, marketingActivityId, remoteId, utm }) => {
    const query = gql`
      mutation UpdateMarketingActivityExternal(
        $input: MarketingActivityUpdateExternalInput!
        $marketingActivityId: ID
        $remoteId: String
        $utm: UTMInput
      ) {
        marketingActivityUpdateExternal(
          input: $input
          marketingActivityId: $marketingActivityId
          remoteId: $remoteId
          utm: $utm
        ) {
          marketingActivity {
            id
            title
            status
            statusLabel
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { input, marketingActivityId, remoteId, utm }));
  },
);

server.tool(
  "delete-marketing-activity-external",
  {
    marketingActivityId: z.string().optional(),
    remoteId: z.string().optional(),
  },
  async ({ marketingActivityId, remoteId }) => {
    const query = gql`
      mutation DeleteMarketingActivityExternal($marketingActivityId: ID, $remoteId: String) {
        marketingActivityDeleteExternal(
          marketingActivityId: $marketingActivityId
          remoteId: $remoteId
        ) {
          deletedMarketingActivityId
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { marketingActivityId, remoteId }));
  },
);

server.tool(
  "get-returns",
  { limit: z.number().default(10) },
  async ({ limit }) => {
    const query = gql`
      query GetReturns($first: Int!) {
        returnableFulfillments(first: $first) {
          edges {
            node {
              id
              fulfillment {
                id
              }
              order {
                id
                name
              }
            }
          }
        }
        returns(first: $first) {
          edges {
            node {
              id
              name
              status
              createdAt
              closedAt
              totalQuantity
              order {
                id
                name
              }
            }
          }
        }
      }
    `;
    return asTextContent(await request(query, { first: limit }));
  },
);

server.tool(
  "create-return",
  { returnInput: freeformObjectSchema },
  async ({ returnInput }) => {
    const query = gql`
      mutation CreateReturn($returnInput: ReturnInput!) {
        returnCreate(returnInput: $returnInput) {
          return {
            id
            name
            status
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { returnInput }));
  },
);

server.tool(
  "approve-return-request",
  { input: freeformObjectSchema },
  async ({ input }) => {
    const query = gql`
      mutation ApproveReturnRequest($input: ReturnApproveRequestInput!) {
        returnApproveRequest(input: $input) {
          return {
            id
            name
            status
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { input }));
  },
);

server.tool(
  "decline-return-request",
  { input: freeformObjectSchema },
  async ({ input }) => {
    const query = gql`
      mutation DeclineReturnRequest($input: ReturnDeclineRequestInput!) {
        returnDeclineRequest(input: $input) {
          return {
            id
            name
            status
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { input }));
  },
);

server.tool(
  "cancel-return",
  { id: z.string().min(1) },
  async ({ id }) => {
    const query = gql`
      mutation CancelReturn($id: ID!) {
        returnCancel(id: $id) {
          return {
            id
            name
            status
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { id }));
  },
);

server.tool(
  "close-return",
  { id: z.string().min(1) },
  async ({ id }) => {
    const query = gql`
      mutation CloseReturn($id: ID!) {
        returnClose(id: $id) {
          return {
            id
            name
            status
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { id }));
  },
);

server.tool(
  "reopen-return",
  { id: z.string().min(1) },
  async ({ id }) => {
    const query = gql`
      mutation ReopenReturn($id: ID!) {
        returnReopen(id: $id) {
          return {
            id
            name
            status
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { id }));
  },
);

server.tool(
  "run-shopifyql-query",
  { query: z.string().min(1) },
  async ({ query }) => {
    const gqlQuery = gql`
      query RunShopifyqlQuery($query: String!) {
        shopifyqlQuery(query: $query) {
          parseErrors
          tableData {
            columns {
              displayName
              name
              dataType
            }
            rows
          }
        }
      }
    `;
    return asTextContent(await request(gqlQuery, { query }));
  },
);

server.tool(
  "get-delivery-customizations",
  { limit: z.number().default(10), searchQuery: z.string().optional() },
  async ({ limit, searchQuery }) => {
    const query = gql`
      query GetDeliveryCustomizations($first: Int!, $query: String) {
        deliveryCustomizations(first: $first, query: $query) {
          edges {
            node {
              id
              title
              enabled
              functionId
            }
          }
        }
      }
    `;
    return asTextContent(await request(query, { first: limit, query: searchQuery }));
  },
);

server.tool(
  "get-payment-customizations",
  { limit: z.number().default(10), searchQuery: z.string().optional() },
  async ({ limit, searchQuery }) => {
    const query = gql`
      query GetPaymentCustomizations($first: Int!, $query: String) {
        paymentCustomizations(first: $first, query: $query) {
          edges {
            node {
              id
              title
              enabled
              functionId
            }
          }
        }
      }
    `;
    return asTextContent(await request(query, { first: limit, query: searchQuery }));
  },
);

server.tool(
  "get-theme-files",
  {
    themeId: z.string().min(1),
    filenames: z.array(z.string()).optional(),
    limit: z.number().default(20),
  },
  async ({ themeId, filenames, limit }) => {
    const query = gql`
      query GetThemeFiles($id: ID!, $filenames: [String!], $first: Int!) {
        theme(id: $id) {
          id
          name
          role
          files(filenames: $filenames, first: $first) {
            edges {
              node {
                filename
                contentType
                checksumMd5
                updatedAt
                body {
                  ... on OnlineStoreThemeFileBodyText {
                    content
                  }
                  ... on OnlineStoreThemeFileBodyBase64 {
                    contentBase64
                  }
                  ... on OnlineStoreThemeFileBodyUrl {
                    url
                  }
                }
              }
            }
          }
        }
      }
    `;
    return asTextContent(await request(query, { id: themeId, filenames, first: limit }));
  },
);

server.tool(
  "upsert-theme-files",
  {
    themeId: z.string().min(1),
    files: z
      .array(
        z.object({
          filename: z.string().min(1),
          body: z.object({
            type: z.enum(["TEXT", "BASE64", "URL"]),
            value: z.string().min(1),
          }),
        }),
      )
      .min(1),
  },
  async ({ themeId, files }) => {
    const query = gql`
      mutation UpsertThemeFiles($themeId: ID!, $files: [OnlineStoreThemeFilesUpsertFileInput!]!) {
        themeFilesUpsert(themeId: $themeId, files: $files) {
          upsertedThemeFiles {
            filename
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { themeId, files }));
  },
);

server.tool(
  "delete-theme-files",
  {
    themeId: z.string().min(1),
    filenames: z.array(z.string().min(1)).min(1),
  },
  async ({ themeId, filenames }) => {
    const query = gql`
      mutation DeleteThemeFiles($themeId: ID!, $filenames: [String!]!) {
        themeFilesDelete(themeId: $themeId, filenames: $filenames) {
          deletedThemeFiles
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { themeId, filenames }));
  },
);

server.tool(
  "get-storefront-access-tokens",
  { limit: z.number().default(10) },
  async ({ limit }) => {
    const query = gql`
      query GetStorefrontAccessTokens($first: Int!) {
        shop {
          storefrontAccessTokens(first: $first) {
            edges {
              node {
                id
                title
                accessToken
                accessScopes {
                  handle
                }
                createdAt
                updatedAt
              }
            }
          }
        }
      }
    `;
    return asTextContent(await request(query, { first: limit }));
  },
);

server.tool(
  "create-storefront-access-token",
  { title: z.string().min(1) },
  async ({ title }) => {
    const query = gql`
      mutation CreateStorefrontAccessToken($input: StorefrontAccessTokenInput!) {
        storefrontAccessTokenCreate(input: $input) {
          storefrontAccessToken {
            id
            title
            accessToken
            accessScopes {
              handle
            }
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { input: { title } }));
  },
);

server.tool(
  "delete-storefront-access-token",
  { id: z.string().min(1) },
  async ({ id }) => {
    const query = gql`
      mutation DeleteStorefrontAccessToken($id: ID!) {
        storefrontAccessTokenDelete(id: $id) {
          deletedStorefrontAccessTokenId
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await request(query, { id }));
  },
);

server.tool(
  "cj-connection-status",
  {},
  async () => {
    const session = await ensureCjSession();
    return asTextContent({
      configured: Boolean(CJ_API_KEY),
      docsUrl: CJ_API_DOCS,
      apiBaseUrl: CJ_API_BASE_URL,
      openId: session.openId,
      accessTokenExpiryDate: session.accessTokenExpiryDate,
      refreshTokenExpiryDate: session.refreshTokenExpiryDate,
    });
  },
);

server.tool(
  "cj-api",
  {
    path: z.string().min(1),
    method: z.enum(["GET", "POST", "PATCH", "DELETE"]).default("GET"),
    query: freeformObjectSchema.optional(),
    body: freeformObjectSchema.optional(),
  },
  async ({ path, method, query, body }) => {
    return asTextContent(await cjRequest({ path, method, query, body }));
  },
);

server.tool(
  "cj-get-categories",
  {},
  async () => asTextContent(await cjRequest({ path: "product/getCategory" })),
);

server.tool(
  "cj-get-products",
  {
    pageNum: z.number().int().min(1).default(1),
    pageSize: z.number().int().min(1).max(200).default(20),
    categoryId: z.string().optional(),
    pid: z.string().optional(),
    productSku: z.string().optional(),
    productName: z.string().optional(),
    productNameEn: z.string().optional(),
    productType: z.string().optional(),
    countryCode: z.string().optional(),
    deliveryTime: z.enum(["24", "48", "72"]).optional(),
    verifiedWarehouse: z.number().int().optional(),
    startInventory: z.number().optional(),
    endInventory: z.number().optional(),
    brandOpenId: z.number().optional(),
    minPrice: z.number().optional(),
    maxPrice: z.number().optional(),
    searchType: z.number().int().optional(),
    minListedNum: z.number().int().optional(),
    maxListedNum: z.number().int().optional(),
    sort: z.enum(["asc", "desc"]).optional(),
    orderBy: z.enum(["createAt", "listedNum"]).optional(),
    supplierId: z.string().optional(),
    isFreeShipping: z.number().int().optional(),
    customizationVersion: z.number().int().optional(),
  },
  async (query) => asTextContent(await cjRequest({ path: "product/list", query })),
);

server.tool(
  "cj-get-product",
  {
    pid: z.string().optional(),
    productSku: z.string().optional(),
    variantSku: z.string().optional(),
    features: z.array(z.string()).optional(),
    countryCode: z.string().optional(),
  },
  async (query) => {
    if (!query.pid && !query.productSku && !query.variantSku) {
      throw new Error("Provide one of pid, productSku, or variantSku.");
    }

    return asTextContent(await cjRequest({ path: "product/query", query }));
  },
);

server.tool(
  "cj-get-variants",
  {
    pid: z.string().optional(),
    productSku: z.string().optional(),
    variantSku: z.string().optional(),
    countryCode: z.string().optional(),
  },
  async (query) => {
    if (!query.pid && !query.productSku && !query.variantSku) {
      throw new Error("Provide one of pid, productSku, or variantSku.");
    }

    return asTextContent(await cjRequest({ path: "product/variant/query", query }));
  },
);

server.tool(
  "cj-get-variant-by-vid",
  {
    vid: z.string().min(1),
    features: z.string().optional(),
  },
  async (query) =>
    asTextContent(await cjRequest({ path: "product/variant/queryByVid", query })),
);

server.tool(
  "cj-get-stock-by-vid",
  { vid: z.string().min(1) },
  async (query) =>
    asTextContent(await cjRequest({ path: "product/stock/queryByVid", query })),
);

server.tool(
  "cj-get-stock-by-sku",
  { sku: z.string().min(1) },
  async (query) =>
    asTextContent(await cjRequest({ path: "product/stock/queryBySku", query })),
);

server.tool(
  "cj-get-inventory-by-pid",
  { pid: z.string().min(1) },
  async (query) =>
    asTextContent(await cjRequest({ path: "product/stock/getInventoryByPid", query })),
);

server.tool(
  "cj-get-orders",
  {
    pageNum: z.number().int().min(1).default(1),
    pageSize: z.number().int().min(1).max(100).default(20),
    orderIds: z.array(z.string()).optional(),
    shipmentOrderId: z.string().optional(),
    status: z
      .enum([
        "CREATED",
        "IN_CART",
        "UNPAID",
        "UNSHIPPED",
        "SHIPPED",
        "DELIVERED",
        "CANCELLED",
        "OTHER",
      ])
      .optional(),
  },
  async (query) => asTextContent(await cjRequest({ path: "shopping/order/list", query })),
);

server.tool(
  "cj-get-order-detail",
  {
    orderId: z.string().min(1),
    features: z.array(z.string()).optional(),
  },
  async (query) =>
    asTextContent(await cjRequest({ path: "shopping/order/getOrderDetail", query })),
);

server.tool(
  "cj-get-balance",
  {},
  async () => asTextContent(await cjRequest({ path: "shopping/pay/getBalance" })),
);

server.tool(
  "cj-configure-webhooks",
  {
    product: cjWebhookTopicSchema.optional(),
    stock: cjWebhookTopicSchema.optional(),
    order: cjWebhookTopicSchema.optional(),
    logistics: cjWebhookTopicSchema.optional(),
  },
  async (body) => {
    if (Object.keys(body).length === 0) {
      throw new Error("Provide at least one webhook topic to configure.");
    }

    return asTextContent(
      await cjRequest({ path: "webhook/set", method: "POST", body }),
    );
  },
);

server.tool(
  "get-storefront-products",
  {
    limit: z.number().default(10),
    searchQuery: z.string().optional(),
  },
  async ({ limit, searchQuery }) => {
    const query = gql`
      query GetStorefrontProducts($first: Int!, $query: String) {
        products(first: $first, query: $query) {
          edges {
            node {
              id
              title
              handle
              vendor
              productType
              availableForSale
              totalInventory
              onlineStoreUrl
              featuredImage {
                url
                altText
              }
              priceRange {
                minVariantPrice {
                  amount
                  currencyCode
                }
                maxVariantPrice {
                  amount
                  currencyCode
                }
              }
            }
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { first: limit, query: searchQuery }));
  },
);

server.tool(
  "get-storefront-product",
  {
    id: z.string().optional(),
    handle: z.string().optional(),
  },
  async ({ id, handle }) => {
    const query = gql`
      query GetStorefrontProduct($id: ID, $handle: String) {
        product(id: $id, handle: $handle) {
          id
          title
          handle
          description
          descriptionHtml
          vendor
          productType
          tags
          availableForSale
          totalInventory
          onlineStoreUrl
          seo {
            title
            description
          }
          featuredImage {
            url
            altText
          }
          variants(first: 20) {
            edges {
              node {
                id
                title
                availableForSale
                quantityAvailable
                sku
                price {
                  amount
                  currencyCode
                }
              }
            }
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { id, handle }));
  },
);

server.tool(
  "get-storefront-collections",
  {
    limit: z.number().default(10),
    searchQuery: z.string().optional(),
  },
  async ({ limit, searchQuery }) => {
    const query = gql`
      query GetStorefrontCollections($first: Int!, $query: String) {
        collections(first: $first, query: $query) {
          edges {
            node {
              id
              title
              handle
              description
              updatedAt
              image {
                url
                altText
              }
            }
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { first: limit, query: searchQuery }));
  },
);

server.tool(
  "get-storefront-collection",
  {
    id: z.string().optional(),
    handle: z.string().optional(),
  },
  async ({ id, handle }) => {
    const query = gql`
      query GetStorefrontCollection($id: ID, $handle: String) {
        collection(id: $id, handle: $handle) {
          id
          title
          handle
          description
          updatedAt
          image {
            url
            altText
          }
          products(first: 20) {
            edges {
              node {
                id
                title
                handle
                availableForSale
              }
            }
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { id, handle }));
  },
);

server.tool(
  "create-storefront-customer",
  {
    email: z.string().email(),
    password: z.string().min(1),
    firstName: z.string().optional(),
    lastName: z.string().optional(),
    phone: z.string().optional(),
    acceptsMarketing: z.boolean().optional(),
  },
  async (input) => {
    const query = gql`
      mutation CreateStorefrontCustomer($input: CustomerCreateInput!) {
        customerCreate(input: $input) {
          customer {
            id
            email
            firstName
            lastName
            phone
            acceptsMarketing
          }
          customerUserErrors {
            field
            message
            code
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { input }));
  },
);

server.tool(
  "create-customer-access-token",
  {
    email: z.string().email(),
    password: z.string().min(1),
  },
  async (input) => {
    const query = gql`
      mutation CreateCustomerAccessToken($input: CustomerAccessTokenCreateInput!) {
        customerAccessTokenCreate(input: $input) {
          customerAccessToken {
            accessToken
            expiresAt
          }
          customerUserErrors {
            field
            message
            code
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { input }));
  },
);

server.tool(
  "renew-customer-access-token",
  { customerAccessToken: z.string().min(1) },
  async ({ customerAccessToken }) => {
    const query = gql`
      mutation RenewCustomerAccessToken($customerAccessToken: String!) {
        customerAccessTokenRenew(customerAccessToken: $customerAccessToken) {
          customerAccessToken {
            accessToken
            expiresAt
          }
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { customerAccessToken }));
  },
);

server.tool(
  "delete-customer-access-token",
  { customerAccessToken: z.string().min(1) },
  async ({ customerAccessToken }) => {
    const query = gql`
      mutation DeleteCustomerAccessToken($customerAccessToken: String!) {
        customerAccessTokenDelete(customerAccessToken: $customerAccessToken) {
          deletedAccessToken
          deletedCustomerAccessTokenId
          userErrors {
            field
            message
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { customerAccessToken }));
  },
);

server.tool(
  "get-storefront-customer",
  { customerAccessToken: z.string().min(1) },
  async ({ customerAccessToken }) => {
    const query = gql`
      query GetStorefrontCustomer($customerAccessToken: String!) {
        customer(customerAccessToken: $customerAccessToken) {
          id
          email
          firstName
          lastName
          phone
          acceptsMarketing
          numberOfOrders
          defaultAddress {
            id
            address1
            address2
            city
            province
            zip
            country
          }
          addresses(first: 20) {
            edges {
              node {
                id
                address1
                address2
                city
                province
                zip
                country
              }
            }
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { customerAccessToken }));
  },
);

server.tool(
  "update-storefront-customer",
  {
    customerAccessToken: z.string().min(1),
    customer: z.object({
      email: z.string().email().optional(),
      password: z.string().optional(),
      firstName: z.string().optional(),
      lastName: z.string().optional(),
      phone: z.string().optional(),
      acceptsMarketing: z.boolean().optional(),
    }),
  },
  async ({ customerAccessToken, customer }) => {
    const query = gql`
      mutation UpdateStorefrontCustomer(
        $customerAccessToken: String!
        $customer: CustomerUpdateInput!
      ) {
        customerUpdate(
          customerAccessToken: $customerAccessToken
          customer: $customer
        ) {
          customer {
            id
            email
            firstName
            lastName
            phone
            acceptsMarketing
          }
          customerAccessToken {
            accessToken
            expiresAt
          }
          customerUserErrors {
            field
            message
            code
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { customerAccessToken, customer }));
  },
);

server.tool(
  "create-cart",
  {
    input: freeformObjectSchema.optional(),
  },
  async ({ input }) => {
    const query = gql`
      mutation CreateCart($input: CartInput) {
        cartCreate(input: $input) {
          cart {
            id
            checkoutUrl
            totalQuantity
            note
            buyerIdentity {
              email
              phone
              countryCode
            }
            lines(first: 20) {
              edges {
                node {
                  id
                  quantity
                  merchandise {
                    ... on ProductVariant {
                      id
                      title
                      product {
                        title
                        handle
                      }
                    }
                  }
                }
              }
            }
          }
          userErrors {
            field
            message
          }
          warnings {
            code
            message
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { input }));
  },
);

server.tool(
  "get-cart",
  { cartId: z.string().min(1) },
  async ({ cartId }) => {
    const query = gql`
      query GetCart($id: ID!) {
        cart(id: $id) {
          id
          checkoutUrl
          totalQuantity
          note
          createdAt
          updatedAt
          buyerIdentity {
            email
            phone
            countryCode
          }
          cost {
            subtotalAmount {
              amount
              currencyCode
            }
            totalAmount {
              amount
              currencyCode
            }
          }
          lines(first: 50) {
            edges {
              node {
                id
                quantity
                merchandise {
                  ... on ProductVariant {
                    id
                    title
                    sku
                    product {
                      title
                      handle
                    }
                  }
                }
              }
            }
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { id: cartId }));
  },
);

server.tool(
  "add-cart-lines",
  {
    cartId: z.string().min(1),
    lines: z.array(freeformObjectSchema).min(1),
  },
  async ({ cartId, lines }) => {
    const query = gql`
      mutation AddCartLines($cartId: ID!, $lines: [CartLineInput!]!) {
        cartLinesAdd(cartId: $cartId, lines: $lines) {
          cart {
            id
            totalQuantity
            checkoutUrl
            lines(first: 50) {
              edges {
                node {
                  id
                  quantity
                }
              }
            }
          }
          userErrors {
            field
            message
          }
          warnings {
            code
            message
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { cartId, lines }));
  },
);

server.tool(
  "update-cart-lines",
  {
    cartId: z.string().min(1),
    lines: z.array(freeformObjectSchema).min(1),
  },
  async ({ cartId, lines }) => {
    const query = gql`
      mutation UpdateCartLines($cartId: ID!, $lines: [CartLineUpdateInput!]!) {
        cartLinesUpdate(cartId: $cartId, lines: $lines) {
          cart {
            id
            totalQuantity
            checkoutUrl
            lines(first: 50) {
              edges {
                node {
                  id
                  quantity
                }
              }
            }
          }
          userErrors {
            field
            message
          }
          warnings {
            code
            message
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { cartId, lines }));
  },
);

server.tool(
  "update-cart-buyer-identity",
  {
    cartId: z.string().min(1),
    buyerIdentity: freeformObjectSchema,
  },
  async ({ cartId, buyerIdentity }) => {
    const query = gql`
      mutation UpdateCartBuyerIdentity(
        $cartId: ID!
        $buyerIdentity: CartBuyerIdentityInput!
      ) {
        cartBuyerIdentityUpdate(cartId: $cartId, buyerIdentity: $buyerIdentity) {
          cart {
            id
            checkoutUrl
            buyerIdentity {
              email
              phone
              countryCode
            }
          }
          userErrors {
            field
            message
          }
          warnings {
            code
            message
          }
        }
      }
    `;
    return asTextContent(await storefrontRequest(query, { cartId, buyerIdentity }));
  },
);

server.tool(
  "storefront-graphql",
  {
    query: z.string().min(1),
    variables: z.record(z.string(), z.any()).optional(),
    storefrontAccessToken: z.string().optional(),
  },
  async ({ query, variables, storefrontAccessToken }) => {
    const result = await storefrontRequest(query, variables || {}, storefrontAccessToken);
    return asTextContent(result);
  },
);

const transport = new StdioServerTransport();
server.connect(transport).catch((error) => {
  console.error("Failed to start extended Shopify MCP server:", error);
});
