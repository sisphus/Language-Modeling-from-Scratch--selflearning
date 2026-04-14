import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// https://vite.dev/config/
export default defineConfig({
  base: process.env.NODE_ENV === "production" ? "/spring2025-lectures/" : "/",
  publicDir: path.resolve(__dirname, ".."),
  plugins: [react()],
  server: {
    fs: {
      allow: [".."],
    },
  },
});
