import type { Config } from "tailwindcss";

/**
 * A production control room, not a chat app.
 *
 * Dark neutral base with exactly two signal colours: `alert` for impact and
 * constraint violations, `clear` for validated and approved. Every other
 * distinction is made with weight and spacing, because a screen where six
 * things are coloured is a screen where nothing reads as urgent.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          900: "#08090c",
          850: "#0d0f14",
          800: "#12151c",
          700: "#1a1e28",
          600: "#252b38",
          500: "#39404f",
        },
        chalk: {
          100: "#f2f4f8",
          200: "#d6dae3",
          400: "#9aa3b4",
          600: "#69738a",
        },
        alert: {
          DEFAULT: "#ff6b4a",
          dim: "#7a2d1c",
          wash: "rgba(255, 107, 74, 0.10)",
        },
        clear: {
          DEFAULT: "#3ddc97",
          dim: "#1a5c42",
          wash: "rgba(61, 220, 151, 0.10)",
        },
        caution: "#f5c451",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem", letterSpacing: "0.04em" }],
      },
      keyframes: {
        "pulse-alert": {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(255, 107, 74, 0.45)" },
          "50%": { boxShadow: "0 0 0 8px rgba(255, 107, 74, 0)" },
        },
        "slide-in": {
          from: { opacity: "0", transform: "translateY(-6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "pulse-alert": "pulse-alert 1.8s ease-out 3",
        "slide-in": "slide-in 220ms ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
