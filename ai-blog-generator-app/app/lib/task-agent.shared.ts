/** Helpers shared by Task Agent loaders and components (safe in the browser). */

/** Encode one URL path segment (ids come from the browser). */
export function seg(value: string | number): string {
  return encodeURIComponent(String(value ?? ""));
}
