import type { LoaderFunctionArgs } from "react-router";
import { redirect, Form, useLoaderData } from "react-router";

import { login } from "../../shopify.server";

import styles from "./styles.module.css";

export const loader = async ({ request }: LoaderFunctionArgs) => {
  const url = new URL(request.url);

  if (url.searchParams.get("shop")) {
    throw redirect(`/app?${url.searchParams.toString()}`);
  }

  return { showForm: Boolean(login) };
};

const FEATURES = [
  {
    icon: "✍️",
    title: "AI-generated blog posts",
    detail:
      "Create SEO-optimised articles in seconds using GPT-4o, Claude, or Gemini — with your brand voice, keywords, and product links baked in.",
  },
  {
    icon: "🗓️",
    title: "Set-and-forget publishing",
    detail:
      "Schedule posts daily, weekly, or on a custom cadence. The scheduler runs automatically in the background — no manual work required.",
  },
  {
    icon: "🔍",
    title: "Built-in quality checks",
    detail:
      "Every draft is scored before publish. Checks cover title SEO, keyword density, word count, heading structure, and image presence.",
  },
  {
    icon: "🖼️",
    title: "Product image integration",
    detail:
      "Automatically pulls images from your Shopify product listings and embeds the best one as the featured blog image.",
  },
  {
    icon: "📊",
    title: "Keyword & title pools",
    detail:
      "Maintain a rotating pool of keywords and title templates. The AI picks from these each run so your content stays fresh and varied.",
  },
  {
    icon: "⚙️",
    title: "Full control from your Admin",
    detail:
      "Manage prompts, AI models, schedules, and settings all inside Shopify Admin — no separate dashboard to log into.",
  },
];

export default function App() {
  const { showForm } = useLoaderData<typeof loader>();

  return (
    <div className={styles.page}>
      {/* Hero */}
      <header className={styles.hero}>
        <div className={styles.badge}>Shopify App</div>
        <h1 className={styles.heading}>
          AI Blog Generator
        </h1>
        <p className={styles.subheading}>
          Publish high-quality, SEO-ready blog content automatically — powered
          by the world's best large language models, connected directly to your
          Shopify store.
        </p>

        {showForm && (
          <Form className={styles.form} method="post" action="/auth/login">
            <div className={styles.formRow}>
              <input
                className={styles.input}
                type="text"
                name="shop"
                placeholder="your-store.myshopify.com"
                autoComplete="off"
                spellCheck={false}
              />
              <button className={styles.button} type="submit">
                Install app →
              </button>
            </div>
            <p className={styles.inputHint}>Enter your Shopify store domain to get started</p>
          </Form>
        )}
      </header>

      {/* Feature grid */}
      <section className={styles.features}>
        <h2 className={styles.featuresHeading}>Everything you need to grow organic traffic</h2>
        <ul className={styles.grid}>
          {FEATURES.map((f) => (
            <li key={f.title} className={styles.card}>
              <span className={styles.cardIcon}>{f.icon}</span>
              <strong className={styles.cardTitle}>{f.title}</strong>
              <p className={styles.cardDetail}>{f.detail}</p>
            </li>
          ))}
        </ul>
      </section>

      {/* How it works */}
      <section className={styles.steps}>
        <h2 className={styles.featuresHeading}>Up and running in minutes</h2>
        <ol className={styles.stepList}>
          <li className={styles.step}>
            <span className={styles.stepNum}>1</span>
            <div>
              <strong>Install the app</strong> — connect it to your Shopify store with one click.
            </div>
          </li>
          <li className={styles.step}>
            <span className={styles.stepNum}>2</span>
            <div>
              <strong>Add your AI model key</strong> — OpenAI, Anthropic, Google, Replicate, or any OpenAI-compatible endpoint.
            </div>
          </li>
          <li className={styles.step}>
            <span className={styles.stepNum}>3</span>
            <div>
              <strong>Write a prompt</strong> — describe your brand, tone, and the kind of content you want to generate.
            </div>
          </li>
          <li className={styles.step}>
            <span className={styles.stepNum}>4</span>
            <div>
              <strong>Set a schedule</strong> — the app handles everything from there, publishing directly to your blog.
            </div>
          </li>
        </ol>
      </section>

      <footer className={styles.footer}>
        <p>AI Blog Generator · Built for Shopify Merchants</p>
      </footer>
    </div>
  );
}
