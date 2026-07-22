import { createHmac, timingSafeEqual } from "node:crypto";

type PreviewKind = "product" | "social" | "video";
type JsonRecord = Record<string, unknown>;

const BACKEND_KEY =
  process.env.AI_BLOG_BACKEND_API_KEY ||
  process.env.BLOG_GENERATOR_API_KEY ||
  "";

function signingSecret(): string {
  return (
    process.env.LANDING_PAGE_IMAGE_SIGNING_SECRET ||
    BACKEND_KEY ||
    process.env.SHOPIFY_API_SECRET ||
    ""
  );
}

function signatureFor(kind: PreviewKind, filename: string): string {
  const secret = signingSecret();
  if (!secret) return "";

  return createHmac("sha256", secret)
    .update(`${kind}:${filename}`)
    .digest("hex");
}

function safeFilename(value: string): boolean {
  return (
    Boolean(value) &&
    value === value.split(/[\\/]/).pop() &&
    !value.includes("..")
  );
}

export function verifyLandingPageImageSignature(
  kind: PreviewKind,
  filename: string,
  suppliedSignature: string,
): boolean {
  if (!safeFilename(filename) || !/^[a-f0-9]{64}$/i.test(suppliedSignature)) {
    return false;
  }

  const expectedSignature = signatureFor(kind, filename);
  if (!expectedSignature) return false;

  return timingSafeEqual(
    Buffer.from(suppliedSignature, "hex"),
    Buffer.from(expectedSignature, "hex"),
  );
}

function previewUrl(kind: PreviewKind, filename: string): string {
  if (!safeFilename(filename)) return "";

  const signature = signatureFor(kind, filename);
  if (!signature) return "";

  const route = kind === "social" ? "social-images" : kind === "video" ? "social-videos" : "images";
  return `/app/landing-pages/${route}/${encodeURIComponent(filename)}?signature=${signature}`;
}

function filenameFromPath(value: unknown): string {
  if (typeof value !== "string") return "";
  return value.split(/[\\/]/).pop() || "";
}

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function addLandingPageAssetPreviewUrls<T>(data: T): T {
  if (!isRecord(data) || !Array.isArray(data.assets)) {
    return data;
  }

  return {
    ...data,
    assets: data.assets.map((asset) => {
      if (!isRecord(asset)) return asset;
      const filename = filenameFromPath(asset?.local_path);
      return {
        ...asset,
        preview_url: previewUrl("product", filename),
      };
    }),
  } as T;
}

export function removeLandingPageAssetPreviewUrls<T>(data: T): T {
  if (!isRecord(data) || !Array.isArray(data.assets)) {
    return data;
  }

  return {
    ...data,
    assets: data.assets.map((asset) => {
      if (!isRecord(asset)) return asset;
      const persistedAsset = { ...asset };
      delete persistedAsset.preview_url;
      return persistedAsset;
    }),
  } as T;
}

export function addSocialImagePreviewUrls<T>(items: T): T {
  if (!Array.isArray(items)) return items;

  return items.map((item) => {
    if (!isRecord(item)) return item;
    const filename = filenameFromPath(item?.image_file);
    const baseUrl = previewUrl("social", filename);
    const version = item?.image_version;
    const versionedUrl =
      baseUrl && (typeof version === "string" || typeof version === "number")
        ? `${baseUrl}&version=${encodeURIComponent(String(version))}`
        : baseUrl;
    const video = isRecord(item?.video) ? item.video : null;
    const videoFilename = filenameFromPath(video?.video_file);
    const videoBaseUrl = previewUrl("video", videoFilename);
    const videoVersion = video?.video_version;
    const videoPreviewUrl =
      videoBaseUrl && (typeof videoVersion === "string" || typeof videoVersion === "number")
        ? `${videoBaseUrl}&version=${encodeURIComponent(String(videoVersion))}`
        : videoBaseUrl;
    return {
      ...item,
      preview_url: versionedUrl,
      video: video ? { ...video, preview_url: videoPreviewUrl } : video,
    };
  }) as T;
}
