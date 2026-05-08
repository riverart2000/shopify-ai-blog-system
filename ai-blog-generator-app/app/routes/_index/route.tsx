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
        <div className={styles.footerInner}>
          <div className={styles.footerBrand}>
            <div className={styles.footerLogo}>
              {/* fingerprint icon inline SVG */}
              <svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22" aria-hidden="true">
                <path d="M17.81 4.47c-.08 0-.16-.02-.23-.06C15.66 3.42 14 3 12.01 3c-1.98 0-3.86.47-5.57 1.41-.24.13-.54.04-.68-.2-.13-.24-.04-.55.2-.68C7.82 2.52 9.86 2 12.01 2c2.13 0 3.99.47 6.03 1.52.26.14.35.43.22.69-.09.18-.26.26-.45.26zm4.28 3.8c-.08 0-.16-.02-.24-.06-1.89-1.08-4.12-1.66-6.38-1.66-2.26 0-4.5.57-6.38 1.65-.25.14-.57.05-.71-.2-.14-.25-.05-.57.2-.71C10.43 5.9 12.89 5.28 15.47 5.28c2.59 0 5.04.62 7.1 1.79.27.15.36.48.22.75-.1.18-.28.25-.7.25zM12 23.5c-1.42 0-2.82-.29-4.11-.85-1.28-.56-2.44-1.4-3.35-2.47-.68-.79-.44-1.96.44-2.43.66-.35 1.42-.16 1.9.47.34.44.74.85 1.18 1.18.96.73 2.05 1.09 3.15 1.09.67 0 1.34-.14 1.95-.41.71-.32 1.32-.82 1.81-1.45.53-.67 1.33-.83 1.98-.44.86.5 1.07 1.67.37 2.46-.75.85-1.67 1.52-2.68 1.99C13.75 23.22 12.88 23.5 12 23.5zm0-3.5c-.81 0-1.61-.19-2.36-.56-.74-.37-1.38-.91-1.87-1.59-.3-.42-.28-1.01.13-1.35.39-.33.97-.3 1.34.06.29.27.62.5.98.66.49.22 1.04.34 1.58.28.56-.06 1.08-.28 1.51-.63.45-.36 1.04-.41 1.44-.08.44.37.48.98.16 1.41-.46.6-1.06 1.09-1.74 1.41C13.49 19.92 12.75 20 12 20zm0-3.5c-.52 0-1.02-.12-1.47-.35-.44-.23-.82-.56-1.1-.97-.26-.37-.22-.89.1-1.22.34-.35.89-.39 1.27-.09.15.12.32.22.51.28.23.08.47.1.7.05.24-.05.46-.17.64-.34.36-.33.92-.35 1.27-.04.38.34.41.91.09 1.27-.3.33-.66.59-1.07.76C13.04 16.41 12.52 16.5 12 16.5zm0-3.5c-.27 0-.53-.04-.78-.12-.26-.08-.5-.2-.72-.36-.27-.19-.36-.55-.21-.84.17-.32.55-.44.88-.3.19.09.4.14.63.14.23 0 .44-.05.63-.14.33-.14.71-.02.88.3.15.29.06.65-.21.84-.22.16-.46.28-.72.36-.25.08-.51.12-.78.12z"/>
              </svg>
              <span className={styles.footerLogoText}>AI Blog Generator</span>
            </div>
            <p className={styles.footerTagline}>
              AI-powered blog content for Shopify merchants. Publish SEO-ready articles automatically — built by RevenueMindProAI.
            </p>
          </div>

          <div className={styles.footerContact}>
            <h4 className={styles.footerContactHeading}>Contact Us</h4>
            <div className={styles.footerContactLinks}>
              <a href="mailto:revenuemindproai@gmail.com" className={styles.footerLink}>revenuemindproai@gmail.com</a>
              <a href="tel:+447834963875" className={styles.footerLink}>+44 7834 963875</a>
            </div>
            <div className={styles.footerSocials}>
              {/* LinkedIn */}
              <a href="https://www.linkedin.com/company/revenuemindproai" target="_blank" rel="noopener noreferrer" aria-label="LinkedIn" className={`${styles.socialBtn} ${styles.socialLinkedin}`}>
                <svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>
              </a>
              {/* X / Twitter */}
              <a href="https://x.com/revenuemindpro" target="_blank" rel="noopener noreferrer" aria-label="X" className={`${styles.socialBtn} ${styles.socialX}`}>
                <svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-4.714-6.231-5.401 6.231H2.742l7.736-8.858L1.5 2.25H8.56l4.265 5.638L18.244 2.25zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>
              </a>
              {/* Facebook */}
              <a href="https://www.facebook.com/revenuemindproai" target="_blank" rel="noopener noreferrer" aria-label="Facebook" className={`${styles.socialBtn} ${styles.socialFacebook}`}>
                <svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>
              </a>
            </div>
          </div>
        </div>

        <div className={styles.footerBottom}>
          <p>Copyright &copy; {new Date().getFullYear()} RevenueMindProAI. All rights reserved.</p>
          <p className={styles.footerCredit}>Designed and Built by RevenueMindProAI</p>
        </div>
      </footer>
    </div>
  );
}
