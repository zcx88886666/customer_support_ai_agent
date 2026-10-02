import type { NextConfig } from "next";

const config: NextConfig = {
  // The CLI preview can lose very small --showConfig output on WSL. The
  // stable TypeScript compiler API performs the same checks deterministically.
  experimental: { useTypeScriptCli: false },
};

export default config;
