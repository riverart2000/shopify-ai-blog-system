import {
  saveGeneratedAssetDataUrl,
  toAbsoluteAssetUrl,
  uploadPublicFilesToShopify,
} from "./studio-assets.server";

type ShopifySessionInfo = {
  shop: string;
  accessToken: string;
};

type ShopifyShop = {
  name?: string;
  domain?: string;
  myshopify_domain?: string;
};

type ShopifyBlog = {
  id: number;
  handle: string;
  title?: string;
};

type ShopifyProduct = {
  id: string;
  handle: string;
  title?: string;
  status?: string;
  featuredImage?: {
    url?: string;
  } | null;
  guideTitle?: { value: string } | null;
  guideUrl?: { value: string } | null;
};

type ShopifyScope = {
  handle: string;
};

export type ShopifyBlogSummary = {
  id: number;
  handle: string;
  title: string;
};

export type ShopifyProductSummary = {
  id: string;
  handle: string;
  title: string;
  status: string;
  imageUrl: string | null;
  guideTitle?: string | null;
  guideUrl?: string | null;
};

export type ShopifyStudioContext = {
  shopName: string;
  myshopifyDomain: string;
  storefrontDomain: string;
  blogs: ShopifyBlogSummary[];
  products: ShopifyProductSummary[];
  scopes: string[];
  missingScopes: string[];
};

export type BlogContentSection = {
  title: string;
  content: string;
};

export type BlogContent = {
  introduction: string;
  sections: BlogContentSection[];
  html?: string;
};

type GeneratedAssetSources = {
  heroSource?: string;
  portraitSource?: string;
  infographicSource?: string;
};

type GenerateDraftResult = {
  draft: GeneratedBlogDraft;
  generatedAssets: GeneratedAssetSources;
};

type GeneratorServiceResponse = {
  title?: string;
  summary?: string;
  keywords?: unknown;
  hashtags?: unknown;
  content?: string;
  images?: unknown;
};

export type ExternalShareConfig = {
  substack: {
    subtitle: string;
    teaser: string;
  };
  quora: {
    questionTitle: string;
    shortAnswer: string;
  };
  pinterest: {
    pinTitle: string;
    pinDescription: string;
    overlayText: string;
    altText: string;
  };
  facebook: {
    postText: string;
  };
  instagram: {
    caption: string;
  };
};

export type GeneratedBlogDraft = {
  title: string;
  slug: string;
  excerpt: string;
  metaDescription: string;
  category: string;
  readTime: string;
  hashtags: string[];
  cta: string;
  author: string;
  content: BlogContent;
  externalShareConfig: ExternalShareConfig;
};

export type GenerateDraftInput = {
  topic: string;
  category: string;
  tone: string;
  authorName: string;
  brandContext: string;
  targetAudience: string;
  ctaGuidance: string;
  promptEnding: string;
  shopName: string;
};

type PublishDraftInput = {
  blogHandle: string;
  publishMode: "draft" | "publish";
  draft: GeneratedBlogDraft;
  baseUrl: string;
  businessName: string;
  brandContext: string;
  heroImagePromptStyle: string;
  generatedAssets?: GeneratedAssetSources;
  relatedProductId?: string;
};

const SHOPIFY_API_VERSION = "2025-10";
const REQUIRED_SHOPIFY_SCOPES = [
  "read_content",
  "write_content",
  "read_files",
  "write_files",
  "read_products",
  "write_products",
];

function asTrimmedString(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeShopDomain(value: string) {
  return value
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/\/.*$/, "")
    .replace(/^www\./i, "")
    .toLowerCase();
}

export function requireShopifySession(session: unknown): ShopifySessionInfo {
  const candidate = session as { shop?: unknown; accessToken?: unknown } | null;
  const shop = normalizeShopDomain(asTrimmedString(candidate?.shop));
  const accessToken = asTrimmedString(candidate?.accessToken);

  if (!shop || !accessToken) {
    throw new Error("Shopify admin session is missing the store domain or access token.");
  }

  return { shop, accessToken };
}

function slugify(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 80);
}

function parseAiJson(raw: string) {
  try {
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    // continue
  }

  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start === -1 || end === -1 || end <= start) {
    throw new Error("AI response did not contain a JSON object.");
  }

  const candidate = raw.slice(start, end + 1);
  try {
    return JSON.parse(candidate) as Record<string, unknown>;
  } catch {
    // continue
  }

  const repaired = candidate
    .replace(/("(?:[^"\\]|\\.)*")/gs, (match) =>
      match.replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t"),
    )
    .replace(/,\s*([}\]])/g, "$1");

  return JSON.parse(repaired) as Record<string, unknown>;
}

function ensureHashtags(value: unknown) {
  if (!Array.isArray(value)) return [] as string[];

  return Array.from(
    new Set(
      value
        .map((entry) => String(entry || "").trim())
        .filter(Boolean)
        .map((entry) => `#${entry.replace(/^#+/, "").replace(/\s+/g, "")}`),
    ),
  ).slice(0, 8);
}

function ensureContent(value: unknown) {
  const raw = value && typeof value === "object"
    ? (value as { introduction?: unknown; sections?: unknown; html?: unknown })
    : {};
  const introduction = asTrimmedString(raw.introduction);
  const html = asTrimmedString(raw.html);
  const sections = Array.isArray(raw.sections)
    ? raw.sections
        .map((section) => ({
          title: asTrimmedString((section as { title?: unknown }).title),
          content: asTrimmedString((section as { content?: unknown }).content),
        }))
        .filter((section) => section.title || section.content)
    : [];

  if (!introduction && !html && sections.length === 0) {
    throw new Error("AI response did not include any article content.");
  }

  return {
    introduction,
    sections,
    ...(html ? { html } : {}),
  } satisfies BlogContent;
}

function getIntroSummary(content: BlogContent) {
  if (content.introduction.trim()) {
    const [firstParagraph] = content.introduction
      .split(/\n\n+/)
      .map((paragraph) => paragraph.trim())
      .filter(Boolean);

    return firstParagraph || "";
  }

  if (content.html?.trim()) {
    const [firstParagraph] = stripHtml(content.html)
      .split(/\n\n+/)
      .map((paragraph) => paragraph.trim())
      .filter(Boolean);

    return firstParagraph || "";
  }

  const [firstParagraph] = content.introduction
    .split(/\n\n+/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);

  return firstParagraph || "";
}

function stripHtml(value: string) {
  return value
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<\s*br\s*\/?>/gi, "\n")
    .replace(/<\s*\/p\s*>/gi, "\n\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\r/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function truncateForMetaDescription(value: string, maxLength = 160) {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) {
    return normalized;
  }

  const truncated = normalized.slice(0, maxLength - 1).trim();
  const lastSpace = truncated.lastIndexOf(" ");
  return `${(lastSpace > 100 ? truncated.slice(0, lastSpace) : truncated).trim()}.`;
}

function estimateReadTimeFromHtml(html: string) {
  const wordCount = stripHtml(html)
    .split(/\s+/)
    .filter(Boolean)
    .length;

  return `${Math.max(1, Math.ceil(wordCount / 200))} min read`;
}

function ensureExternalShareConfig(value: unknown, draft: {
  title: string;
  excerpt: string;
  metaDescription: string;
  cta: string;
  content: BlogContent;
}) {
  const raw = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const rawSubstack = raw.substack && typeof raw.substack === "object" ? raw.substack as Record<string, unknown> : {};
  const rawQuora = raw.quora && typeof raw.quora === "object" ? raw.quora as Record<string, unknown> : {};
  const rawPinterest = raw.pinterest && typeof raw.pinterest === "object" ? raw.pinterest as Record<string, unknown> : {};
  const rawFacebook = raw.facebook && typeof raw.facebook === "object" ? raw.facebook as Record<string, unknown> : {};
  const rawInstagram = raw.instagram && typeof raw.instagram === "object" ? raw.instagram as Record<string, unknown> : {};
  const introSummary = getIntroSummary(draft.content);

  return {
    substack: {
      subtitle: asTrimmedString(rawSubstack.subtitle) || draft.metaDescription || draft.excerpt,
      teaser: asTrimmedString(rawSubstack.teaser) || draft.excerpt || introSummary || draft.metaDescription,
    },
    quora: {
      questionTitle: asTrimmedString(rawQuora.questionTitle) || draft.title,
      shortAnswer: asTrimmedString(rawQuora.shortAnswer) || draft.excerpt || introSummary || draft.metaDescription,
    },
    pinterest: {
      pinTitle: asTrimmedString(rawPinterest.pinTitle) || draft.title,
      pinDescription: asTrimmedString(rawPinterest.pinDescription) || draft.metaDescription || draft.excerpt || draft.cta,
      overlayText: asTrimmedString(rawPinterest.overlayText) || draft.title,
      altText: asTrimmedString(rawPinterest.altText) || draft.title,
    },
    facebook: {
      postText: asTrimmedString(rawFacebook.postText) || `${draft.excerpt}${draft.cta ? ` ${draft.cta}` : ""}`.trim(),
    },
    instagram: {
      caption: asTrimmedString(rawInstagram.caption) || `${draft.excerpt}${draft.cta ? ` ${draft.cta}` : ""}`.trim(),
    },
  } satisfies ExternalShareConfig;
}

function renderTextBlocksHtml(value: string) {
  return value
    .split("\n\n")
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block) => {
      if (/^\d+\.\s/.test(block)) {
        const items = block
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean)
          .map((line) => `<li>${escapeHtml(line.replace(/^\d+\.\s*/, ""))}</li>`)
          .join("");
        return `<ol>${items}</ol>`;
      }

      if (/^[-•]\s/.test(block)) {
        const items = block
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean)
          .map((line) => `<li>${escapeHtml(line.replace(/^[-•]\s*/, ""))}</li>`)
          .join("");
        return `<ul>${items}</ul>`;
      }

      return block
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => `<p>${escapeHtml(line)}</p>`)
        .join("");
    })
    .join("");
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderBlogHtml(draft: GeneratedBlogDraft) {
  if (draft.content.html?.trim()) {
    return draft.content.html.trim();
  }

  const sections: string[] = [];

  if (draft.content.introduction.trim()) {
    sections.push(`<section>${renderTextBlocksHtml(draft.content.introduction)}</section>`);
  }

  for (const section of draft.content.sections) {
    const heading = section.title.trim() ? `<h2>${escapeHtml(section.title.trim())}</h2>` : "";
    const body = section.content.trim() ? renderTextBlocksHtml(section.content) : "";
    if (heading || body) {
      sections.push(`<section>${heading}${body}</section>`);
    }
  }

  if (draft.cta.trim()) {
    sections.push(`<p><strong>${escapeHtml(draft.cta.trim())}</strong></p>`);
  }

  return sections.join("");
}

function renderSummaryHtml(draft: GeneratedBlogDraft) {
  const summary = draft.excerpt.trim() || draft.metaDescription.trim();
  return summary ? `<p>${escapeHtml(summary)}</p>` : "";
}

function normalizeTags(tags: string[]) {
  return tags
    .map((tag) => tag.trim().replace(/^#+/, ""))
    .filter(Boolean)
    .join(", ");
}

async function shopifyRequest<T>(
  session: ShopifySessionInfo,
  pathname: string,
  init?: RequestInit,
) {
  const response = await fetch(`https://${session.shop}/admin/api/${SHOPIFY_API_VERSION}${pathname}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Shopify-Access-Token": session.accessToken,
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(`Shopify API ${response.status}: ${message || response.statusText}`);
  }

  return response.json() as Promise<T>;
}

async function shopifyGraphqlRequest<T>(
  session: ShopifySessionInfo,
  query: string,
  variables?: Record<string, unknown>,
) {
  const response = await fetch(`https://${session.shop}/admin/api/${SHOPIFY_API_VERSION}/graphql.json`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Shopify-Access-Token": session.accessToken,
    },
    body: JSON.stringify({ query, variables }),
    cache: "no-store",
  });

  const bodyText = await response.text();
  let payload: { data?: T; errors?: Array<{ message?: string }> };

  try {
    payload = JSON.parse(bodyText) as { data?: T; errors?: Array<{ message?: string }> };
  } catch {
    throw new Error(`Shopify GraphQL ${response.status}: ${bodyText || response.statusText}`);
  }

  if (!response.ok) {
    const message = payload.errors?.map((entry) => entry.message).filter(Boolean).join("; ");
    throw new Error(`Shopify GraphQL ${response.status}: ${message || bodyText || response.statusText}`);
  }

  if (payload.errors?.length) {
    throw new Error(payload.errors.map((entry) => entry.message).filter(Boolean).join("; ") || "Shopify GraphQL request failed.");
  }

  if (!payload.data) {
    throw new Error("Shopify GraphQL response did not include data.");
  }

  return payload.data;
}

async function loadShopDetails(session: ShopifySessionInfo) {
  const response = await shopifyRequest<{ shop: ShopifyShop }>(
    session,
    "/shop.json?fields=name,domain,myshopify_domain",
  );

  return response.shop;
}

async function loadBlogs(session: ShopifySessionInfo) {
  const response = await shopifyRequest<{ blogs?: ShopifyBlog[] }>(
    session,
    "/blogs.json?fields=id,handle,title&limit=250",
  );

  return (response.blogs || []).map((blog) => ({
    id: blog.id,
    handle: blog.handle,
    title: blog.title || blog.handle,
  }));
}

async function loadProducts(session: ShopifySessionInfo) {
  try {
    const response = await shopifyGraphqlRequest<{
      products: {
        nodes: ShopifyProduct[];
      };
    }>(
      session,
      `
        query LoadStudioProducts($first: Int!) {
          products(first: $first, sortKey: TITLE) {
            nodes {
              id
              handle
              title
              status
              featuredImage {
                url
              }
              guideTitle: metafield(namespace: "custom", key: "ai_blog_related_guide_title") {
                value
              }
              guideUrl: metafield(namespace: "custom", key: "ai_blog_related_guide_url") {
                value
              }
            }
          }
        }
      `,
      { first: 250 },
    );

    return response.products.nodes.map((product) => ({
      id: product.id,
      handle: product.handle,
      title: product.title || product.handle,
      status: product.status || "ACTIVE",
      imageUrl: product.featuredImage?.url || null,
      guideTitle: product.guideTitle?.value || null,
      guideUrl: product.guideUrl?.value || null,
    })) satisfies ShopifyProductSummary[];
  } catch (error) {
    const message = error instanceof Error ? error.message : "";
    if (/access denied|read_products|write_products/i.test(message)) {
      return [] as ShopifyProductSummary[];
    }
    throw error;
  }
}

async function loadScopes(session: ShopifySessionInfo) {
  try {
    const response = await shopifyRequest<{ access_scopes?: ShopifyScope[] }>(
      session,
      "/access_scopes.json",
    );

    return (response.access_scopes || []).map((scope) => scope.handle);
  } catch (error) {
    const message = error instanceof Error ? error.message : "";
    if (message.includes("Shopify API 404")) {
      return [] as string[];
    }
    throw error;
  }
}

export async function loadShopifyStudioContext(session: ShopifySessionInfo) {
  const [shop, blogs, products, scopes] = await Promise.all([
    loadShopDetails(session),
    loadBlogs(session),
    loadProducts(session),
    loadScopes(session),
  ]);

  return {
    shopName: shop.name || session.shop,
    myshopifyDomain: shop.myshopify_domain || session.shop,
    storefrontDomain: shop.domain || shop.myshopify_domain || session.shop,
    blogs,
    products,
    scopes,
    missingScopes: REQUIRED_SHOPIFY_SCOPES.filter((scope) => !scopes.includes(scope)),
  } satisfies ShopifyStudioContext;
}

function getBlogBackendBaseUrl() {
  return asTrimmedString(
    process.env.AI_BLOG_BACKEND_URL
      || process.env.BLOG_GENERATOR_API_URL
      || "http://127.0.0.1:4000",
  ).replace(/\/$/, "");
}

function getBlogBackendApiKeyHeader() {
  return asTrimmedString(process.env.AI_BLOG_BACKEND_API_KEY_HEADER) || "x-api-key";
}

function getBlogBackendApiKey() {
  return asTrimmedString(process.env.AI_BLOG_BACKEND_API_KEY || process.env.BLOG_GENERATOR_API_KEY);
}

function getBlogBackendTimeoutMs() {
  const raw = Number.parseInt(asTrimmedString(process.env.AI_BLOG_BACKEND_TIMEOUT_MS) || "65000", 10);
  return Number.isFinite(raw) && raw > 0 ? raw : 65000;
}

function extractBackendErrorMessage(statusText: string, bodyText: string) {
  if (!bodyText.trim()) {
    return statusText || "unknown backend error";
  }

  try {
    const payload = JSON.parse(bodyText) as { error?: unknown };
    const message = asTrimmedString(payload.error);
    if (message) {
      return message;
    }
  } catch {
    // fall back to plain text
  }

  return bodyText.trim();
}

async function callBlogGeneratorBackend(prompt: string) {
  const apiKey = getBlogBackendApiKey();
  if (!apiKey) {
    throw new Error("AI blog backend API key is not configured for the Shopify app.");
  }

  const response = await fetch(`${getBlogBackendBaseUrl()}/api/generate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      [getBlogBackendApiKeyHeader()]: apiKey,
    },
    body: JSON.stringify({ prompt }),
    signal: AbortSignal.timeout(getBlogBackendTimeoutMs()),
  });

  const bodyText = await response.text();
  if (!response.ok) {
    throw new Error(
      `AI blog backend failed (${response.status}): ${extractBackendErrorMessage(response.statusText, bodyText)}`,
    );
  }

  let payload: GeneratorServiceResponse;
  try {
    payload = JSON.parse(bodyText) as GeneratorServiceResponse;
  } catch (error) {
    throw new Error(
      `AI blog backend returned invalid JSON: ${error instanceof Error ? error.message : "unknown parse error"}`,
    );
  }

  const title = asTrimmedString(payload.title);
  const content = asTrimmedString(payload.content);
  if (!title || !content) {
    throw new Error("AI blog backend returned an incomplete blog payload.");
  }

  return payload;
}

function buildDraftFromBackendResponse(
  input: GenerateDraftInput,
  response: GeneratorServiceResponse,
): GenerateDraftResult {
  const author = input.authorName.trim() || `${input.shopName} Team`;
  const category = input.category.trim() || "Blog";
  const summary = asTrimmedString(response.summary) || stripHtml(asTrimmedString(response.content)).slice(0, 220);
  const html = asTrimmedString(response.content);
  const draftBase = {
    title: asTrimmedString(response.title) || `${input.shopName} blog draft`,
    slug: slugify(asTrimmedString(response.title) || `${input.shopName}-blog-draft`) || "shopify-blog-draft",
    excerpt: summary,
    metaDescription: truncateForMetaDescription(summary),
    category,
    readTime: estimateReadTimeFromHtml(html),
    hashtags: ensureHashtags(response.hashtags),
    cta: `Explore what ${input.shopName} can do for your next step.`,
    author,
    content: {
      introduction: summary,
      sections: [],
      html,
    },
  } satisfies Omit<GeneratedBlogDraft, "externalShareConfig">;

  const images = Array.isArray(response.images)
    ? response.images.map((entry) => asTrimmedString(entry)).filter(Boolean)
    : [];

  return {
    draft: {
      ...draftBase,
      externalShareConfig: ensureExternalShareConfig({}, draftBase),
    },
    generatedAssets: {
      heroSource: images[0],
      portraitSource: images[1],
      infographicSource: images[2],
    },
  } satisfies GenerateDraftResult;
}

export function isBlogBackendConfigured() {
  return Boolean(getBlogBackendApiKey());
}

export async function generateAiBlogDraft(input: GenerateDraftInput) {
  const topic = input.topic.trim();
  const promptEnding = input.promptEnding.trim();

  if (!topic && !promptEnding) {
    throw new Error("Enter a topic or provide enough prompt-ending context to generate the article.");
  }

  const generationPrompt = [topic.trim(), promptEnding.trim()]
    .filter(Boolean)
    .join("\n\n");
  const response = await callBlogGeneratorBackend(generationPrompt);
  return buildDraftFromBackendResponse(input, response);
}

export function parseDraftFromFormData(formData: FormData) {
  const content = parseAiJson(asTrimmedString(formData.get("draftContentJson")) || "{}");
  const hashtags = JSON.parse(asTrimmedString(formData.get("draftHashtagsJson")) || "[]") as unknown;
  const externalShareConfig = parseAiJson(asTrimmedString(formData.get("draftExternalShareConfigJson")) || "{}");

  const draftBase = {
    title: asTrimmedString(formData.get("draftTitle")),
    slug: slugify(asTrimmedString(formData.get("draftSlug"))),
    excerpt: asTrimmedString(formData.get("draftExcerpt")),
    metaDescription: asTrimmedString(formData.get("draftMetaDescription")),
    category: asTrimmedString(formData.get("draftCategory")) || "Blog",
    readTime: asTrimmedString(formData.get("draftReadTime")) || "6 min read",
    hashtags: ensureHashtags(hashtags),
    cta: asTrimmedString(formData.get("draftCta")),
    author: asTrimmedString(formData.get("draftAuthor")),
    content: ensureContent(content),
  };

  if (!draftBase.title || !draftBase.slug) {
    throw new Error("The generated draft payload was incomplete. Generate the draft again before publishing.");
  }

  return {
    ...draftBase,
    externalShareConfig: ensureExternalShareConfig(externalShareConfig, draftBase),
  } satisfies GeneratedBlogDraft;
}

async function setProductRelatedGuideMetafields(
  session: ShopifySessionInfo,
  input: {
    productId: string;
    guideTitle: string;
    guideUrl: string;
    guideExcerpt: string;
  },
) {
  const metafields = [
    {
      ownerId: input.productId,
      namespace: "$app",
      key: "related_guide_title",
      type: "single_line_text_field",
      value: input.guideTitle,
    },
    {
      ownerId: input.productId,
      namespace: "$app",
      key: "related_guide_url",
      type: "url",
      value: input.guideUrl,
    },
    ...(input.guideExcerpt
      ? [{
          ownerId: input.productId,
          namespace: "$app",
          key: "related_guide_excerpt",
          type: "multi_line_text_field",
          value: input.guideExcerpt,
        }]
      : []),
    {
      ownerId: input.productId,
      namespace: "custom",
      key: "ai_blog_related_guide_title",
      type: "single_line_text_field",
      value: input.guideTitle,
    },
    {
      ownerId: input.productId,
      namespace: "custom",
      key: "ai_blog_related_guide_url",
      type: "url",
      value: input.guideUrl,
    },
    ...(input.guideExcerpt
      ? [{
          ownerId: input.productId,
          namespace: "custom",
          key: "ai_blog_related_guide_excerpt",
          type: "multi_line_text_field",
          value: input.guideExcerpt,
        }]
      : []),
  ];

  const response = await shopifyGraphqlRequest<{
    metafieldsSet: {
      userErrors: Array<{
        field?: string[] | null;
        message: string;
      }>;
    };
  }>(
    session,
    `
      mutation SetProductRelatedGuide($metafields: [MetafieldsSetInput!]!) {
        metafieldsSet(metafields: $metafields) {
          userErrors {
            field
            message
          }
        }
      }
    `,
    { metafields },
  );

  const userErrors = response.metafieldsSet.userErrors || [];
  if (userErrors.length > 0) {
    throw new Error(
      userErrors
        .map((entry) => {
          const field = entry.field?.length ? `${entry.field.join(".")}: ` : "";
          return `${field}${entry.message}`;
        })
        .join("; "),
    );
  }
}

function buildProductGuideBlockEditorUrl(myshopifyDomain: string) {
  const apiKey = asTrimmedString(process.env.SHOPIFY_API_KEY);
  const shop = normalizeShopDomain(myshopifyDomain);

  if (!apiKey || !shop) {
    return null;
  }

  const params = new URLSearchParams({
    template: "product",
    addAppBlockId: `${apiKey}/related-product-guide`,
    target: "mainSection",
  });

  return `https://${shop}/admin/themes/current/editor?${params.toString()}`;
}

export async function publishDraftToShopify(
  session: ShopifySessionInfo,
  input: PublishDraftInput,
) {
  const context = await loadShopifyStudioContext(session);
  const blogHandle = asTrimmedString(input.blogHandle);
  const blog = context.blogs.find((entry) => entry.handle === blogHandle);
  const relatedProduct = input.relatedProductId
    ? context.products.find((entry) => entry.id === input.relatedProductId) || null
    : null;

  if (!blog) {
    const available = context.blogs.map((entry) => entry.handle).join(", ");
    throw new Error(
      available
        ? `Shopify blog handle \"${blogHandle}\" was not found. Available handles: ${available}`
        : "No Shopify blogs were returned for this store.",
    );
  }

  let heroAsset: { publicPath: string } | null = null;
  let shopifyFiles: Array<{ id?: string; url?: string; alt?: string; status?: string }> = [];
  let shopifyFilesError: string | null = null;

  const localAssets: Array<{ label: string; publicPath: string }> = [];

  const resolveGeneratedAsset = async (source: string | undefined, prefix: string) => {
    const normalized = asTrimmedString(source);
    if (!normalized) {
      return null;
    }

    if (/^data:image\//i.test(normalized)) {
      const asset = await saveGeneratedAssetDataUrl({
        shop: session.shop,
        dataUrl: normalized,
        prefix,
      });

      return { publicPath: asset.publicPath };
    }

    if (/^https?:\/\//i.test(normalized)) {
      return { publicPath: normalized };
    }

    throw new Error("Generated asset sources must be either data URLs or absolute URLs.");
  };

  try {
    heroAsset = await resolveGeneratedAsset(input.generatedAssets?.heroSource, `${input.draft.slug}-hero`);
  } catch (error) {
    shopifyFilesError = error instanceof Error ? error.message : "Failed to process the generated hero image.";
  }

  if (heroAsset) {
    localAssets.push({ label: "Hero image", publicPath: heroAsset.publicPath });
  }

  try {
    const asset = await resolveGeneratedAsset(input.generatedAssets?.portraitSource, `${input.draft.slug}-portrait`);
    if (asset) {
      localAssets.push({ label: "Portrait thumbnail", publicPath: asset.publicPath });
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to process the generated portrait image.";
    shopifyFilesError = shopifyFilesError ? `${shopifyFilesError} ${message}` : message;
  }

  try {
    const asset = await resolveGeneratedAsset(input.generatedAssets?.infographicSource, `${input.draft.slug}-infographic`);
    if (asset) {
      localAssets.push({ label: "Infographic portrait", publicPath: asset.publicPath });
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to process the generated infographic image.";
    shopifyFilesError = shopifyFilesError ? `${shopifyFilesError} ${message}` : message;
  }

  try {
    shopifyFiles = await uploadPublicFilesToShopify({
      session,
      files: localAssets.map((asset) => ({
        originalSource: toAbsoluteAssetUrl(input.baseUrl, asset.publicPath),
        alt: `${input.draft.title} ${asset.label.toLowerCase()}`,
        contentType: "IMAGE" as const,
      })),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to upload generated assets to Shopify Files.";
    shopifyFilesError = shopifyFilesError ? `${shopifyFilesError} ${message}` : message;
  }

  const payload = {
    article: {
      title: input.draft.title,
      author: input.draft.author,
      handle: input.draft.slug,
      body_html: renderBlogHtml(input.draft),
      summary_html: renderSummaryHtml(input.draft),
      tags: normalizeTags(input.draft.hashtags),
      published: input.publishMode === "publish",
      ...(input.publishMode === "publish"
        ? { published_at: new Date().toISOString() }
        : {}),
      image: heroAsset
        ? {
            src: toAbsoluteAssetUrl(input.baseUrl, heroAsset.publicPath),
            alt: input.draft.title,
          }
        : undefined,
      metafields: input.draft.metaDescription
        ? [
            {
              namespace: "global",
              key: "description_tag",
              type: "single_line_text_field",
              value: input.draft.metaDescription,
            },
          ]
        : undefined,
    },
  };

  const response = await shopifyRequest<{ article: { id: number; handle: string } }>(
    session,
    `/blogs/${blog.id}/articles.json`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );

  const articleUrl = context.storefrontDomain
    ? `https://${normalizeShopDomain(context.storefrontDomain)}/blogs/${blog.handle}/${response.article.handle}`
    : null;

  let relatedProductLink: {
    productId: string;
    productHandle: string;
    productTitle: string;
    status: "linked" | "failed";
    error?: string;
  } | null = null;

  if (input.relatedProductId && !relatedProduct) {
    relatedProductLink = {
      productId: input.relatedProductId,
      productHandle: "",
      productTitle: "",
      status: "failed",
      error: "The selected product was not available when the blog was published.",
    };
  } else if (relatedProduct) {
    if (!articleUrl) {
      relatedProductLink = {
        productId: relatedProduct.id,
        productHandle: relatedProduct.handle,
        productTitle: relatedProduct.title,
        status: "failed",
        error: "The article was published, but the storefront URL could not be resolved for the product page block.",
      };
    } else {
      try {
        await setProductRelatedGuideMetafields(session, {
          productId: relatedProduct.id,
          guideTitle: input.draft.title,
          guideUrl: articleUrl,
          guideExcerpt: input.draft.excerpt || input.draft.metaDescription,
        });
        relatedProductLink = {
          productId: relatedProduct.id,
          productHandle: relatedProduct.handle,
          productTitle: relatedProduct.title,
          status: "linked",
        };
      } catch (error) {
        relatedProductLink = {
          productId: relatedProduct.id,
          productHandle: relatedProduct.handle,
          productTitle: relatedProduct.title,
          status: "failed",
          error: error instanceof Error ? error.message : "The article was published, but the product page metafields could not be updated.",
        };
      }
    }
  }

  return {
    articleId: response.article.id,
    articleHandle: response.article.handle,
    articleUrl,
    mode: input.publishMode,
    blogTitle: blog.title,
    blogHandle: blog.handle,
    localAssets,
    shopifyFiles,
    shopifyFilesError,
    relatedProductLink,
    productGuideBlockEditorUrl: buildProductGuideBlockEditorUrl(context.myshopifyDomain),
  };
}