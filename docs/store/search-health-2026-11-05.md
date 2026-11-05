# Search and Shopping health fixes, 5 November 2026

A sanity check of Merchant Center and Search Console found the shop set up correctly but barely seen:
22 search clicks in three months, 425 pages indexed against 2,060 not, and under 20 product impressions
in 28 days. These fixes were applied the same day.

## Applied

| Fix | What changed | Undo |
|---|---|---|
| Dead URLs | 266 URL redirects in Shopify for pages Google reported as 404 (deleted or moved blog posts and products). 130 go to the same post at its new address, the rest to the nearest blog or collection. A redirect for `/blogs/x` also covers `/fr/blogs/x`. | Delete the redirects listed in `.runtime/m5/backups/redirects-created-20261105.json` |
| 404 re-check | "Validate fix" started in Search Console for the 444 "Not found (404)" pages | none needed |
| Yoga mat images | Foldable EVA Travel Yoga Mat: the 12 variant photos with printed size text (Google: "promotional overlay on image") replaced with the same photos without the text; the 14 old text photos removed | Originals in `.runtime/m5/backups/gmc-images-20261105/` |
| Search Console and Merchant Center | Associated (Search Console → Merchant opportunities) | Search Console → Settings → Associations |

## Left for the owner

- US return policy in Merchant Center (return window and cost show "Incomplete").
- Store rating: Google Customer Reviews has shown no opt-in for over 30 days.
- Delivery time in the Google & YouTube channel (8–9 days, rated "Fair").
- Google will add 199 products it found on the site by itself on 24 November unless that is turned off.
- Three products carry a supplier "Ships From" option that ends up in Google titles
  (Handcrafted Brass Singing Bowl, Contoured Foam Resistance Thigh Trainer, Natural Stone Owl Bead Bracelet).
- The Open-Ear Bone Conduction Headphones listing is rejected because the product no longer exists in the shop;
  it drops out of the feed without action.
- Bing Places and Bing Webmaster Tools were not checked: Chrome was not signed in to Microsoft.
