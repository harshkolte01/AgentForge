"use client";
import { useSyncExternalStore } from "react";
import { getEnterSends, getTheme, subscribePreferences, type ThemePreference } from "@/lib/preferences";

/** The theme the person chose for this browser ("system" on the server, where there is no choice to read). */
export function useThemePreference(): ThemePreference {
  return useSyncExternalStore(subscribePreferences, getTheme, () => "system");
}

/** Whether Enter sends a message (otherwise Enter adds a line and Cmd/Ctrl+Enter sends). */
export function useEnterSends(): boolean {
  return useSyncExternalStore(subscribePreferences, getEnterSends, () => true);
}
