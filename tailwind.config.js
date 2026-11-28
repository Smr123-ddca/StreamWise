/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        imdb: {
          gold: "#F5C518",
          black: "#161616",
          white: "#F5F5F5",
          silver: "#969696",
          blue: "#00ADD9",
          panel: "#222222",
          raised: "#292929",
          line: "#343434"
        }
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"]
      },
      boxShadow: {
        gold: "0 0 0 1px rgba(245,197,24,.18), 0 8px 30px rgba(245,197,24,.07)"
      }
    }
  },
  plugins: []
};