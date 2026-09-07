import type { Config } from "tailwindcss";

/**
 * The palette comes from the objects PRI replaces, and there are two of them:
 * the strip board on the office wall (dark) and the call sheet on the desk
 * (light). The values live in `globals.css` as CSS variables; this file only
 * names them, so a component never knows which theme is on.
 *
 * The board:
 *
 * Before any of this was software, a 1st AD scheduled a film on a wooden board
 * holding one cardboard strip per scene. The strips are colour-coded, and the
 * code has been the same for decades:
 *
 *     white   INT / DAY        blue    INT / NIGHT
 *     yellow  EXT / DAY        green   EXT / NIGHT
 *
 * So colour here is never decoration — it is the scene's `int_ext` and
 * `time_of_day`, and a 1st AD reads the shape of a week without reading a word.
 * That is also why the board is dark and the strips are light: the card is the
 * thing you read, and the frame is only what holds it.
 *
 * The one non-board colour is `stamp`, an ink red used for exactly one thing:
 * a plan the validator refused. A rejection on a call sheet is a rubber stamp,
 * not a neon alert, so it is dull and inky rather than bright.
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Every colour is a CSS variable so both themes share one set of names.
      // `<alpha-value>` keeps Tailwind's opacity modifiers (`bg-stamp/20`)
      // working through the indirection.
      colors: {
        board: {
          900: "rgb(var(--board-900) / <alpha-value>)",
          800: "rgb(var(--board-800) / <alpha-value>)",
          700: "rgb(var(--board-700) / <alpha-value>)",
          600: "rgb(var(--board-600) / <alpha-value>)",
          500: "rgb(var(--board-500) / <alpha-value>)",
        },
        chalk: {
          100: "rgb(var(--chalk-100) / <alpha-value>)",
          200: "rgb(var(--chalk-200) / <alpha-value>)",
          400: "rgb(var(--chalk-400) / <alpha-value>)",
          600: "rgb(var(--chalk-600) / <alpha-value>)",
        },
        strip: {
          "int-day": "rgb(var(--strip-int-day) / <alpha-value>)",
          "ext-day": "rgb(var(--strip-ext-day) / <alpha-value>)",
          "int-night": "rgb(var(--strip-int-night) / <alpha-value>)",
          "ext-night": "rgb(var(--strip-ext-night) / <alpha-value>)",
          held: "rgb(var(--strip-held) / <alpha-value>)",
        },
        ink: "rgb(var(--ink) / <alpha-value>)",
        stamp: {
          DEFAULT: "rgb(var(--stamp) / <alpha-value>)",
          dim: "rgb(var(--stamp-dim) / <alpha-value>)",
          wash: "rgb(var(--stamp) / 0.12)",
        },
        seal: {
          DEFAULT: "rgb(var(--seal) / <alpha-value>)",
          dim: "rgb(var(--seal-dim) / <alpha-value>)",
          wash: "rgb(var(--seal) / 0.12)",
        },
        caution: "rgb(var(--caution) / <alpha-value>)",
      },
      fontFamily: {
        // Archivo: a grotesque with a real condensed cut, which is what a
        // narrow strip needs. Courier Prime: because a screenplay is Courier,
        // so scene slugs are set in the face they were written in.
        sans: ["var(--font-archivo)", "ui-sans-serif", "system-ui", "sans-serif"],
        condensed: ["var(--font-archivo-condensed)", "var(--font-archivo)", "sans-serif"],
        script: ["var(--font-courier-prime)", "ui-monospace", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      keyframes: {
        "stamp-in": {
          "0%": { opacity: "0", transform: "scale(1.4) rotate(-4deg)" },
          "60%": { opacity: "1", transform: "scale(0.96) rotate(-2deg)" },
          "100%": { opacity: "1", transform: "scale(1) rotate(-2deg)" },
        },
        "slide-in": {
          from: { opacity: "0", transform: "translateY(-6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        // One orchestrated moment, on the thing the demo turns on.
        "stamp-in": "stamp-in 320ms cubic-bezier(0.2, 0.9, 0.3, 1.4) 1",
        "slide-in": "slide-in 220ms ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
