import prisma from "../db.server";

export type StudioPromptEndingPreset = {
  id: string;
  name: string;
  text: string;
};

export type StudioPromptEndingConfig = {
  selectedId: string;
  presets: StudioPromptEndingPreset[];
  selectedPreset: StudioPromptEndingPreset;
};

export type StudioSettingsSnapshot = {
  businessName: string;
  businessLogoUrl: string;
  defaultAuthorName: string;
  brandContext: string;
  targetAudience: string;
  ctaGuidance: string;
  defaultBlogHandle: string;
  preferredProvider: "deepseek" | "grok";
  heroImagePromptStyle: string;
  socialDestinations: {
    substackPublicationUrl: string;
    quoraSpaceUrl: string;
    pinterestBoardUrl: string;
    facebookUrl: string;
    instagramUrl: string;
  };
  promptEndingConfig: StudioPromptEndingConfig;
};

type SettingsDefaults = {
  shopName: string;
  defaultBlogHandle: string;
};

const DEFAULT_PROMPT_ENDING_TEXT = [
  "Write for a premium wellness brand with precise, practical advice.",
  "Keep the tone warm, expert, and commercially useful without becoming salesy.",
  "Make the article feel ready for publication inside a Shopify storefront blog.",
].join(" ");

const DEFAULT_HERO_IMAGE_PROMPT_STYLE = "Luxury wellness photography with gold and green brand cues, editorial composition, premium skincare tone.";

function asTrimmedString(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeProvider(value: unknown, fallback: "deepseek" | "grok") {
  return String(value || "").trim() === "grok" ? "grok" : fallback;
}

function parsePromptEndingPresets(value: string | null | undefined) {
  if (!value) return [] as StudioPromptEndingPreset[];

  try {
    const parsed = JSON.parse(value);
    if (!Array.isArray(parsed)) return [] as StudioPromptEndingPreset[];

    return parsed
      .map((preset) => {
        if (!preset || typeof preset !== "object") return null;
        const id = asTrimmedString((preset as { id?: unknown }).id);
        const name = asTrimmedString((preset as { name?: unknown }).name);
        const text = asTrimmedString((preset as { text?: unknown }).text);

        if (!id || !name || !text) return null;
        return { id, name, text } satisfies StudioPromptEndingPreset;
      })
      .filter((preset): preset is StudioPromptEndingPreset => Boolean(preset));
  } catch {
    return [] as StudioPromptEndingPreset[];
  }
}

function getDefaultPromptEndingPreset() {
  return {
    id: "default",
    name: "Default prompt ending",
    text: DEFAULT_PROMPT_ENDING_TEXT,
  } satisfies StudioPromptEndingPreset;
}

function buildPromptEndingConfig(input: {
  promptEndingPresetsJson?: string | null;
  selectedPromptEndingId?: string | null;
}) {
  const presets = parsePromptEndingPresets(input.promptEndingPresetsJson);
  const availablePresets = presets.length > 0 ? presets : [getDefaultPromptEndingPreset()];
  const selectedId = asTrimmedString(input.selectedPromptEndingId);
  const selectedPreset = availablePresets.find((preset) => preset.id === selectedId) ?? availablePresets[0];

  return {
    selectedId: selectedPreset.id,
    presets: availablePresets,
    selectedPreset,
  } satisfies StudioPromptEndingConfig;
}

export async function loadStudioSettings(shop: string, defaults: SettingsDefaults) {
  const record = await prisma.studioSettings.findUnique({ where: { shop } });
  const businessName = asTrimmedString(record?.businessName) || defaults.shopName;
  const preferredProviderFallback = process.env.DEEPSEEK_API_KEY ? "deepseek" : "grok";
  const promptEndingConfig = buildPromptEndingConfig({
    promptEndingPresetsJson: record?.promptEndingPresetsJson,
    selectedPromptEndingId: record?.selectedPromptEndingId,
  });

  return {
    businessName,
    businessLogoUrl: asTrimmedString(record?.businessLogoUrl),
    defaultAuthorName: asTrimmedString(record?.defaultAuthorName) || `${businessName} Team`,
    brandContext: asTrimmedString(record?.brandContext),
    targetAudience: asTrimmedString(record?.targetAudience),
    ctaGuidance: asTrimmedString(record?.ctaGuidance),
    defaultBlogHandle: asTrimmedString(record?.defaultBlogHandle) || defaults.defaultBlogHandle,
    preferredProvider: normalizeProvider(record?.preferredProvider, preferredProviderFallback),
    heroImagePromptStyle: asTrimmedString(record?.heroImagePromptStyle) || DEFAULT_HERO_IMAGE_PROMPT_STYLE,
    socialDestinations: {
      substackPublicationUrl: asTrimmedString(record?.substackPublicationUrl),
      quoraSpaceUrl: asTrimmedString(record?.quoraSpaceUrl),
      pinterestBoardUrl: asTrimmedString(record?.pinterestBoardUrl),
      facebookUrl: asTrimmedString(record?.facebookUrl) || asTrimmedString(process.env.FACEBOOK),
      instagramUrl: asTrimmedString(record?.instagramUrl) || asTrimmedString(process.env.INSTAGRAM),
    },
    promptEndingConfig,
  } satisfies StudioSettingsSnapshot;
}

export async function saveStudioSettings(
  shop: string,
  input: {
    businessName: string;
    businessLogoUrl: string;
    defaultAuthorName: string;
    brandContext: string;
    targetAudience: string;
    ctaGuidance: string;
    defaultBlogHandle: string;
    preferredProvider: "deepseek" | "grok";
    heroImagePromptStyle: string;
    substackPublicationUrl: string;
    quoraSpaceUrl: string;
    pinterestBoardUrl: string;
    facebookUrl: string;
    instagramUrl: string;
  },
) {
  await prisma.studioSettings.upsert({
    where: { shop },
    update: {
      businessName: asTrimmedString(input.businessName) || null,
      businessLogoUrl: asTrimmedString(input.businessLogoUrl) || null,
      defaultAuthorName: asTrimmedString(input.defaultAuthorName) || null,
      brandContext: asTrimmedString(input.brandContext) || null,
      targetAudience: asTrimmedString(input.targetAudience) || null,
      ctaGuidance: asTrimmedString(input.ctaGuidance) || null,
      defaultBlogHandle: asTrimmedString(input.defaultBlogHandle) || null,
      preferredProvider: normalizeProvider(input.preferredProvider, "deepseek"),
      heroImagePromptStyle: asTrimmedString(input.heroImagePromptStyle) || null,
      substackPublicationUrl: asTrimmedString(input.substackPublicationUrl) || null,
      quoraSpaceUrl: asTrimmedString(input.quoraSpaceUrl) || null,
      pinterestBoardUrl: asTrimmedString(input.pinterestBoardUrl) || null,
      facebookUrl: asTrimmedString(input.facebookUrl) || null,
      instagramUrl: asTrimmedString(input.instagramUrl) || null,
    },
    create: {
      shop,
      businessName: asTrimmedString(input.businessName) || null,
      businessLogoUrl: asTrimmedString(input.businessLogoUrl) || null,
      defaultAuthorName: asTrimmedString(input.defaultAuthorName) || null,
      brandContext: asTrimmedString(input.brandContext) || null,
      targetAudience: asTrimmedString(input.targetAudience) || null,
      ctaGuidance: asTrimmedString(input.ctaGuidance) || null,
      defaultBlogHandle: asTrimmedString(input.defaultBlogHandle) || null,
      preferredProvider: normalizeProvider(input.preferredProvider, "deepseek"),
      heroImagePromptStyle: asTrimmedString(input.heroImagePromptStyle) || null,
      substackPublicationUrl: asTrimmedString(input.substackPublicationUrl) || null,
      quoraSpaceUrl: asTrimmedString(input.quoraSpaceUrl) || null,
      pinterestBoardUrl: asTrimmedString(input.pinterestBoardUrl) || null,
      facebookUrl: asTrimmedString(input.facebookUrl) || null,
      instagramUrl: asTrimmedString(input.instagramUrl) || null,
    },
  });
}

export async function savePromptEndingPreset(
  shop: string,
  input: { presetId?: string | null; name: string; text: string; makeSelected?: boolean },
  defaults: SettingsDefaults,
) {
  const current = await loadStudioSettings(shop, defaults);
  const name = asTrimmedString(input.name);
  const text = asTrimmedString(input.text);

  if (!name) {
    throw new Error("Prompt ending name is required.");
  }

  if (!text) {
    throw new Error("Prompt ending text is required.");
  }

  const presetId = asTrimmedString(input.presetId) || `preset-${Date.now()}`;
  const presets = [...current.promptEndingConfig.presets];
  const index = presets.findIndex((preset) => preset.id === presetId);
  const nextPreset = { id: presetId, name, text } satisfies StudioPromptEndingPreset;

  if (index >= 0) {
    presets[index] = nextPreset;
  } else {
    presets.push(nextPreset);
  }

  const selectedPromptEndingId = input.makeSelected === false
    ? current.promptEndingConfig.selectedId
    : presetId;

  await prisma.studioSettings.upsert({
    where: { shop },
    update: {
      promptEndingPresetsJson: JSON.stringify(presets),
      selectedPromptEndingId,
    },
    create: {
      shop,
      promptEndingPresetsJson: JSON.stringify(presets),
      selectedPromptEndingId,
    },
  });
}

export async function selectPromptEndingPreset(
  shop: string,
  presetId: string,
  defaults: SettingsDefaults,
) {
  const current = await loadStudioSettings(shop, defaults);
  const selected = current.promptEndingConfig.presets.find((preset) => preset.id === asTrimmedString(presetId));

  if (!selected) {
    throw new Error("Prompt ending preset not found.");
  }

  await prisma.studioSettings.upsert({
    where: { shop },
    update: {
      selectedPromptEndingId: selected.id,
    },
    create: {
      shop,
      selectedPromptEndingId: selected.id,
      promptEndingPresetsJson: JSON.stringify(current.promptEndingConfig.presets),
    },
  });
}