import path from "path";
import vue from "@vitejs/plugin-vue";
import AutoImport from "unplugin-auto-import/vite";

export default {
  plugins: [
    vue(),
    AutoImport({
      imports: ["vue", "@vueuse/core", "vue-i18n"],
      dts: false,
    }),
  ],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./app/tests/setup.ts"],
    server: {
      deps: {
        // Vuetify ships its dist modules importing their own .css, which Node's ESM loader
        // rejects with `Unknown file extension ".css"` when the package is left external.
        // Inlining lets Vite transform them, which is what makes it possible to mount real
        // Vuetify components in a component test instead of hand-written stand-ins.
        inline: ["vuetify"],
      },
    },
    coverage: {
      provider: "v8",
      include: ["app/{lib,components,composables,layouts,pages}/**/*.{ts,tsx,vue}"],
      exclude: [
        "**/*.test.*",
        "node_modules/**",
        "dist/**",
        "coverage/**",
        "**/__tests__/**",
        "app/lib/icons/**",
        "app/lib/api/types/**",
      ],
      reporter: ["html", "text-summary"],
      all: true,
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./app"),
      "~": path.resolve(__dirname, "./app"),
      "@@": path.resolve(__dirname, "."),
      "~~": path.resolve(__dirname, "."),
    },
  },
};
