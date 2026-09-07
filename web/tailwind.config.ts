import type { Config } from "tailwindcss";

/**
 * The palette comes from the object PRI replaces: a production strip board.
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
      colors: {
        // The board itself: warm and wooden, not a blue-black screen.
        board: {
          900: "#12100c",
          800: "#191612",
          700: "#221d17",
          600: "#2e2820",
          500: "#413931",
        },
        // Text on the board.
        chalk: {
          100: "#f0ebe0",
          200: "#d9d2c4",
          400: "#9c9384",
          600: "#6b6357",
        },
        // The four strip colours, held back so a board of them is readable
        // rather than a fairground.
        strip: {
          "int-day": "#e9e3d4",
          "ext-day": "#e0c163",
          "int-night": "#7fa3c9",
          "ext-night": "#8bab72",
          held: "#3a332b",
        },
        // Ink printed on a strip.
        ink: "#191510",
        // Refused. One use, one meaning.
        stamp: {
          DEFAULT: "#c0453b",
          dim: "#5e241f",
          wash: "rgba(192, 69, 59, 0.12)",
        },
        // Approved, verified, live.
        seal: {
          DEFAULT: "#5f9e6a",
          dim: "#2c4a31",
          wash: "rgba(95, 158, 106, 0.12)",
        },
        caution: "#d19a3f",
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
