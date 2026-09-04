import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Сборка кладётся в web/dist и коммитится в репозиторий: на демонстрации
// Node не нужен, всё отдаёт один процесс uvicorn.
//
// base: "/static/" — приложение монтируется FastAPI по этому пути.
// Ассеты инлайнятся агрессивно, потому что контур офлайн: чем меньше
// отдельных запросов, тем меньше поводов для отказа.
export default defineConfig({
  plugins: [react()],
  base: "/static/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    assetsInlineLimit: 8192,
    // Один бандл вместо десятка чанков: приложение небольшое, а меньше
    // файлов — меньше шансов, что что-то не отдастся.
    rollupOptions: { output: { manualChunks: undefined } },
  },
  server: {
    port: 5173,
    // В режиме разработки API проксируется на uvicorn.
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true, ws: false },
    },
  },
});
