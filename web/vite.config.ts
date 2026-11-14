import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建产物直接落到 Coordinator 的静态资源目录，由后端 / 路径托管。
// base 固定为 "/"，产物 index.html 引用 /assets/... 绝对路径。
export default defineConfig({
  base: "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 开发模式：浏览器请求 /api/v1/* 由 Vite 转发到本机 Coordinator
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "../packages/cef-coordinator/src/cef_coordinator/web",
    emptyOutDir: true,
    assetsDir: "assets",
    target: "es2020",
    sourcemap: false,
  },
});
