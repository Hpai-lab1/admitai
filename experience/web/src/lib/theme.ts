import type { CSSProperties } from "react";

export type Theme = {
  primary_color: string;
  secondary_color: string;
  accent_color: string;
  font: "system" | "montserrat" | "inter" | "noto-sans";
  logo_url?: string | null;
};

export const DEFAULT_THEME: Theme = {
  primary_color: "#1E3A8A",
  secondary_color: "#3B82F6",
  accent_color: "#111827",
  font: "system",
};

/** "#E34A21" -> "227 74 33" (the form Tailwind's rgb(var(--x) / alpha) needs). */
export function hexToTriplet(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m || !m[1]) return "0 0 0";
  const n = parseInt(m[1], 16);
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`;
}

function luminance(hex: string): number {
  const [r, g, b] = hexToTriplet(hex)
    .split(" ")
    .map((v) => {
      const c = Number(v) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two colours (1-21). */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** White or near-black text, whichever reads better on the colour (WCAG 2.2 AA needs 4.5:1). */
export function textOn(hex: string): string {
  return contrast(hex, "#ffffff") >= contrast(hex, "#111111") ? "#ffffff" : "#111111";
}

const FONTS: Record<Theme["font"], string> = {
  system: "system-ui, -apple-system, 'Segoe UI', sans-serif",
  montserrat: "var(--font-montserrat), system-ui, sans-serif",
  inter: "var(--font-inter), system-ui, sans-serif",
  "noto-sans": "var(--font-noto), system-ui, sans-serif",
};

/** Darken a colour step by step until white text on it meets WCAG AA (4.5:1). Brand hue is kept. */
export function accessible(hex: string, ratio = 4.5): string {
  let [r, g, b] = hexToTriplet(hex).split(" ").map(Number) as [number, number, number];
  const toHex = () => "#" + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
  for (let i = 0; i < 60 && contrast(toHex(), "#ffffff") < ratio; i++) {
    [r, g, b] = [r * 0.96, g * 0.96, b * 0.96];
  }
  return toHex().toUpperCase();
}

const DARK_SURFACE = "#1E1E24";

/** Lighten a colour towards white until it reads on the dark-mode surface (4.5:1). */
export function onDark(hex: string, ratio = 4.5): string {
  let [r, g, b] = hexToTriplet(hex).split(" ").map(Number) as [number, number, number];
  const toHex = () => "#" + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
  for (let i = 0; i < 60 && contrast(toHex(), DARK_SURFACE) < ratio; i++) {
    [r, g, b] = [r + (255 - r) * 0.08, g + (255 - g) * 0.08, b + (255 - b) * 0.08];
  }
  return toHex().toUpperCase();
}

export function themeStyle(t: Partial<Theme> | null | undefined): CSSProperties {
  const theme = { ...DEFAULT_THEME, ...(t ?? {}) };
  // Buttons, links and the header carry small white text, so the brand colours are nudged darker
  // where needed to keep WCAG 2.2 AA contrast (PRD: accessibility is a release requirement).
  return {
    "--brand": hexToTriplet(accessible(theme.primary_color)),
    "--brand2": hexToTriplet(accessible(theme.secondary_color)),
    "--accent": hexToTriplet(accessible(theme.accent_color)),
    "--brand-on-dark": hexToTriplet(onDark(theme.primary_color)),
    "--brand2-on-dark": hexToTriplet(onDark(theme.secondary_color)),
    "--accent-on-dark": hexToTriplet(onDark(theme.accent_color)),
    "--on-brand": "#ffffff",
    "--brand-font": FONTS[theme.font] ?? FONTS.system,
  } as CSSProperties;
}
