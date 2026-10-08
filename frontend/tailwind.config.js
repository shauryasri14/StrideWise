/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: { fog: "#F1F5F4", ink: "#12292B", moss: "#2E6B57", mist: "#D5E0DC", best: "#0B7A75", clean: "#6A5BC4", quiet: "#B8741A" },
      // System fonts only: a privacy-first app should not call a font CDN.
      fontFamily: { sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"] },
    },
  },
  plugins: [],
};
