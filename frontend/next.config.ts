import type { NextConfig } from "next";

// Static export so the deployed backend can serve the UI from the same origin.
const config: NextConfig = {
  output: "export",
  trailingSlash: true,
};

export default config;
