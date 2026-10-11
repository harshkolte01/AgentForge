/**
 * Browser-local preferences: the colour theme, whether Enter sends, and the sidebar width. They belong to this browser, not the
 * account, so they live in `localStorage` (which can be missing or throw in private windows; every access is guarded and the
 * defaults apply). Each preference is a tiny external store for `useSyncExternalStore`, and a change in another tab is picked up.
 */
export type ThemePreference = "system" | "light" | "dark";

export const THEME_KEY = "agentforge.theme";
export const ENTER_SENDS_KEY = "agentforge.enter-sends";
export const SIDEBAR_WIDTH_KEY = "agentforge.sidebar.width";
export const DELIVERY_WIDTH_KEY = "agentforge.delivery.width";

export type StorageLike = { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void };

function storage(): StorageLike | null {
  try { return typeof localStorage === "undefined" ? null : localStorage; } catch { return null; }
}
function read(key: string): string | null {
  try { return storage()?.getItem(key) ?? null; } catch { return null; }
}
function write(key: string, value: string | null) {
  try { if (value === null) storage()?.removeItem(key); else storage()?.setItem(key, value); } catch { /* The choice still applies to this page. */ }
}

export function parseTheme(value: string | null): ThemePreference {
  return value === "light" || value === "dark" ? value : "system";
}
/** Enter sends unless the person turned it off. */
export function parseEnterSends(value: string | null): boolean {
  return value !== "false";
}
/** Whether the page should be dark for a preference, given whether the system asks for dark. */
export function resolveDark(preference: ThemePreference, systemDark: boolean): boolean {
  return preference === "dark" || (preference === "system" && systemDark);
}

/**
 * Runs in `<head>` before the first paint (see `app/layout.tsx`), so a dark page never flashes light, and keeps "System"
 * following the operating system's theme live (and a choice made in another tab) on every page. It must stay self-contained:
 * it is serialized as a string. `applyTheme` below does the same work when the choice changes in this tab.
 */
export const themeScript = `(function(){try{var k=${JSON.stringify(THEME_KEY)},m=window.matchMedia("(prefers-color-scheme: dark)"),e=document.documentElement;function a(){var p=null;try{p=localStorage.getItem(k)}catch(_){}var d=p==="dark"||(p!=="light"&&m.matches);e.classList.toggle("dark",d);e.style.colorScheme=d?"dark":"light"}a();m.addEventListener("change",a);window.addEventListener("storage",function(v){if(v.key===k)a()})}catch(_){}})();`;

const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());
let watching = false;
let systemQuery: MediaQueryList | null = null;

function systemDark(): boolean {
  return !!systemQuery?.matches;
}
export function applyTheme(preference: ThemePreference = parseTheme(read(THEME_KEY))) {
  if (typeof document === "undefined") return;
  const dark = resolveDark(preference, systemDark());
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}
/** Follows the system theme live (for "System") and choices made in other tabs. Idempotent. */
function watch() {
  if (watching || typeof window === "undefined") return;
  watching = true;
  systemQuery = window.matchMedia?.("(prefers-color-scheme: dark)") ?? null;
  systemQuery?.addEventListener("change", () => { applyTheme(); notify(); });
  window.addEventListener("storage", (event) => {
    if (event.key === THEME_KEY) applyTheme();
    if (event.key === THEME_KEY || event.key === ENTER_SENDS_KEY || event.key === SIDEBAR_WIDTH_KEY) notify();
  });
}

export function subscribePreferences(listener: () => void): () => void {
  watch();
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export const getTheme = (): ThemePreference => parseTheme(read(THEME_KEY));
export function setTheme(preference: ThemePreference) {
  write(THEME_KEY, preference === "system" ? null : preference);
  applyTheme(preference);
  notify();
}

export const getEnterSends = (): boolean => parseEnterSends(read(ENTER_SENDS_KEY));
export function setEnterSends(value: boolean) {
  write(ENTER_SENDS_KEY, value ? null : "false");
  notify();
}

export const readDeliveryWidth = (): string | null => read(DELIVERY_WIDTH_KEY);
export function writeDeliveryWidth(value: number | null) {
  write(DELIVERY_WIDTH_KEY, value === null ? null : String(value));
}

export const readSidebarWidth = (): string | null => read(SIDEBAR_WIDTH_KEY);
export function writeSidebarWidth(value: number | null) {
  write(SIDEBAR_WIDTH_KEY, value === null ? null : String(value));
  notify();
}
