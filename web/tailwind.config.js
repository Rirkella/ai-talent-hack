/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  // Тёмная тема переключается классом на <html>, а не медиазапросом:
  // выбор темы — решение пользователя, а не системы.
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        brand: { DEFAULT: "#00AAFF", dark: "#0088CC" },
      },
    },
  },
  plugins: [],
};
