import type { MediaRef } from "../../lib/social-engine.types";
import { humanize } from "./vocab";

/** One rendered asset (image or video) through the signed media proxy. */
export function MediaPreview({ media, className = "sa-media", alt = "" }: { media: MediaRef; className?: string; alt?: string }) {
  if (!media.url) {
    return <div className={`sa-placeholder ${className}`} style={{ aspectRatio: aspect(media) }}>Preview unavailable</div>;
  }
  if (media.kind === "video") {
    return (
      <video className={className} src={media.url} controls preload="metadata" playsInline style={{ aspectRatio: aspect(media) }}>
        <track kind="captions" />
      </video>
    );
  }
  return <img className={className} src={media.url} alt={alt} loading="lazy" style={{ aspectRatio: aspect(media) }} />;
}

function aspect(media: MediaRef): string | undefined {
  return media.width && media.height ? `${media.width} / ${media.height}` : undefined;
}

export function mediaCaption(media: MediaRef): string {
  const size = media.width && media.height ? ` · ${media.width}×${media.height}` : "";
  const duration = media.duration_ms ? ` · ${(media.duration_ms / 1000).toFixed(1)}s` : "";
  const role = media.role && media.role !== "primary" ? ` · ${humanize(media.role)}` : "";
  return `${humanize(media.format || media.kind)}${size}${duration}${role}`;
}

/** The small preview on queue cards. */
export function PostPreviewImage({ url, media, title }: { url?: string; media?: MediaRef[]; title: string }) {
  const firstVideo = (media || []).find((item) => item.kind === "video");
  if (!url && firstVideo?.url) {
    return <MediaPreview media={firstVideo} className="sa-preview" />;
  }
  if (!url) {
    return <div className="sa-placeholder sa-preview" style={{ aspectRatio: "1 / 1" }}>No image yet</div>;
  }
  return <img className="sa-preview" src={url} alt={`Preview of ${title}`} loading="lazy" />;
}
