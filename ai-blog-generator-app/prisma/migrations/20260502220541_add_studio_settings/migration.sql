-- CreateTable
CREATE TABLE "StudioSettings" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "shop" TEXT NOT NULL,
    "businessName" TEXT,
    "businessLogoUrl" TEXT,
    "defaultAuthorName" TEXT,
    "brandContext" TEXT,
    "targetAudience" TEXT,
    "ctaGuidance" TEXT,
    "defaultBlogHandle" TEXT,
    "preferredProvider" TEXT,
    "heroImagePromptStyle" TEXT,
    "promptEndingPresetsJson" TEXT,
    "selectedPromptEndingId" TEXT,
    "substackPublicationUrl" TEXT,
    "quoraSpaceUrl" TEXT,
    "pinterestBoardUrl" TEXT,
    "facebookUrl" TEXT,
    "instagramUrl" TEXT,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL
);

-- CreateIndex
CREATE UNIQUE INDEX "StudioSettings_shop_key" ON "StudioSettings"("shop");
