/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        command: { cyan: "#22e1c1", amber: "#ffb63d", ink: "#050a09" },
      },
    },
  },
  plugins: [],
};
