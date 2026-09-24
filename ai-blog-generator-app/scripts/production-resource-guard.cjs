const fs = require("node:fs");
const path = require("node:path");

const sentinel = path.resolve(__dirname, "..", "..", ".production-no-frontend-build");
const blocked = process.env.DISABLE_FRONTEND_BUILD_ON_PRODUCTION === "1"
  || fs.existsSync(sentinel);

if (blocked) {
  const event = process.env.npm_lifecycle_event || "npm command";
  console.error("");
  console.error("============================================================");
  console.error("BLOCKED: frontend dependency installation/build is disabled");
  console.error("on this production server because it can exhaust CPU/RAM and");
  console.error("crash the live Shopify application.");
  console.error("");
  console.error(`Attempted lifecycle: ${event}`);
  console.error("Build on the development machine, then deploy the completed");
  console.error("ai-blog-generator-app/build/ directory with the deploy script.");
  console.error("See SERVER_README.md: 'Critical rule: never build the frontend on production'.");
  console.error("============================================================");
  console.error("");
  process.exit(1);
}
