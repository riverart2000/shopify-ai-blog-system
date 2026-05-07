import { mkdir, writeFile } from "fs/promises";
import path from "path";

type ShopifySessionInfo = {
  shop: string;
  accessToken: string;
};

function asTrimmedString(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function getExtensionForMimeType(mimeType: string) {
  if (mimeType === "image/png") return "png";
  if (mimeType === "image/webp") return "webp";
  if (mimeType === "image/jpeg") return "jpg";
  if (mimeType === "image/svg+xml") return "svg";
  return null;
}

function normalizeShopSlug(shop: string) {
  return shop.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase();
}

export async function saveGeneratedAssetDataUrl(input: {
  shop: string;
  dataUrl: string;
  prefix: string;
}) {
  const match = input.dataUrl.match(/^data:(image\/[a-zA-Z0-9.+-]+);base64,(.+)$/);
  if (!match) {
    throw new Error("Invalid generated asset data.");
  }

  const [, mimeType, payload] = match;
  const extension = getExtensionForMimeType(mimeType);
  if (!extension) {
    throw new Error(`Unsupported asset type: ${mimeType}`);
  }

  const assetDir = path.join(process.cwd(), "public", "generated-assets", normalizeShopSlug(input.shop));
  await mkdir(assetDir, { recursive: true });

  const filename = `${input.prefix}-${Date.now()}.${extension}`;
  const outputPath = path.join(assetDir, filename);
  await writeFile(outputPath, Buffer.from(payload, "base64"));

  return {
    mimeType,
    publicPath: `/generated-assets/${normalizeShopSlug(input.shop)}/${filename}`,
  };
}

export function toAbsoluteAssetUrl(baseUrl: string, assetPath: string) {
  if (/^https?:\/\//i.test(assetPath)) return assetPath;
  return `${baseUrl.replace(/\/$/, "")}/${assetPath.replace(/^\//, "")}`;
}

export async function uploadPublicFilesToShopify(input: {
  session: ShopifySessionInfo;
  files: Array<{ originalSource: string; alt?: string; contentType?: "IMAGE" | "FILE" }>;
}) {
  const files = input.files.filter((file) => Boolean(file.originalSource.trim()));
  if (files.length === 0) {
    return [] as Array<{ id?: string; url?: string; alt?: string; status?: string }>;
  }

  const query = `mutation fileCreate($files: [FileCreateInput!]!) {
    fileCreate(files: $files) {
      files {
        __typename
        ... on MediaImage {
          id
          alt
          status
          image {
            url
          }
        }
        ... on GenericFile {
          id
          alt
          url
        }
      }
      userErrors {
        field
        message
      }
    }
  }`;

  const response = await fetch(`https://${input.session.shop}/admin/api/2025-10/graphql.json`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Shopify-Access-Token": input.session.accessToken,
    },
    body: JSON.stringify({
      query,
      variables: {
        files: files.map((file) => ({
          originalSource: file.originalSource,
          alt: file.alt || undefined,
          contentType: file.contentType || "IMAGE",
        })),
      },
    }),
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(`Shopify file upload failed (${response.status}): ${message || response.statusText}`);
  }

  const payload = await response.json() as {
    data?: {
      fileCreate?: {
        files?: Array<{
          id?: string;
          alt?: string | null;
          status?: string | null;
          image?: { url?: string | null } | null;
          url?: string | null;
        }>;
        userErrors?: Array<{ message?: string | null }>;
      };
    };
    errors?: Array<{ message?: string }>;
  };

  const errors = payload.errors?.map((error) => error.message || "").filter(Boolean) || [];
  const userErrors = payload.data?.fileCreate?.userErrors?.map((error) => error.message || "").filter(Boolean) || [];
  if (errors.length > 0 || userErrors.length > 0) {
    throw new Error([...errors, ...userErrors].join("; "));
  }

  return (payload.data?.fileCreate?.files || []).map((file) => ({
    id: file.id,
    alt: file.alt || undefined,
    status: file.status || undefined,
    url: file.image?.url || file.url || undefined,
  }));
}