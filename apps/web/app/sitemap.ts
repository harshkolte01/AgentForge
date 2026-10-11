import type { MetadataRoute } from "next";
import { siteUrl } from "@/lib/site";

/** Only the landing page is public content; the sign-in pages and the app are not for search. */
export default function sitemap(): MetadataRoute.Sitemap {
  return [{ url: siteUrl, changeFrequency: "monthly", priority: 1 }];
}
