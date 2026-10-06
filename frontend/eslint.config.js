import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "src/shared/api/schema.gen.ts", "test-results", "playwright-report"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "@typescript-eslint/consistent-type-imports": "error",
    },
  },
  {
    // 分区约束：调试台与用户端互不导入，两者只依赖 shared
    files: ["src/user/**/*.{ts,tsx}"],
    rules: { "no-restricted-imports": ["error", { patterns: ["@/debug/*", "**/debug/*"] }] },
  },
  {
    files: ["src/debug/**/*.{ts,tsx}"],
    rules: { "no-restricted-imports": ["error", { patterns: ["@/user/*", "**/user/*"] }] },
  },
);
