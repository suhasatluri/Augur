import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        background: "#08090D",
        foreground: "#E8E6E1",
        gold: {
          DEFAULT: "#C9A84C",
          dark: "#A68A3E",
          light: "#D4BC6A",
        },
        surface: {
          DEFAULT: "#0F1117",
          light: "#161822",
          border: "#1E2030",
        },
        muted: "#6B7280",
      },
      fontFamily: {
        heading: ["var(--font-cormorant)", "Georgia", "serif"],
        mono: ["var(--font-jetbrains)", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
