import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { bundleWorkflowCode } from "@temporalio/worker";

const workflowsPath = fileURLToPath(new URL("../src/workflows.ts", import.meta.url));
const bundlePath = fileURLToPath(new URL("./workflow-bundle.js", import.meta.url));
const bundle = await bundleWorkflowCode({
  workflowsPath,
  webpackConfigHook(config) {
    // The workflow source is TypeScript and is mapped by Temporal's SWC rule. Re-processing every
    // emitted JavaScript source map (including the SDK itself) is redundant and can stall Webpack.
    if (config.module?.rules) {
      config.module.rules = config.module.rules.filter((rule) => {
        if (!rule || typeof rule !== "object" || !("test" in rule)) return true;
        return String(rule.test) !== "/\\.js$/";
      });
    }
    return config;
  },
});
await fs.writeFile(bundlePath, bundle.code, { encoding: "utf8", mode: 0o600 });
