import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api/presenter": {
        target: process.env.PRESENTER_API_URL ?? "http://127.0.0.1:5190",
        changeOrigin: true,
        timeout: 300_000,
        proxyTimeout: 300_000,
      },
    },
  },
  test: { include: ["tests/**/*.test.ts"] },
});
