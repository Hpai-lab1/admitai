import type { Config } from "tailwindcss";

// Colours come from CSS variables set per college (see src/lib/theme.ts), so one build serves every tenant.
const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: "rgb(var(--brand) / <alpha-value>)",
        brand2: "rgb(var(--brand2) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        // neutral tokens flip in dark mode (globals.css)
        ink: "rgb(var(--c-ink) / <alpha-value>)",
        paper: "rgb(var(--c-paper) / <alpha-value>)",
        surface: "rgb(var(--c-surface) / <alpha-value>)",
        muted: "rgb(var(--c-muted) / <alpha-value>)",
        line: "rgb(var(--c-line) / <alpha-value>)",
        subtle: "rgb(var(--c-subtle) / <alpha-value>)",
      },
      fontFamily: { brand: ["var(--brand-font)", "system-ui", "sans-serif"] },
      borderRadius: { btn: "0.7em" },
    },
  },
  plugins: [],
};
export default config;
