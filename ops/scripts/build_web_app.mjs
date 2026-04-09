import { build } from "esbuild";

const root = "/home/ubuntu/vllm_jetson/bartlebyGPT";

await build({
  absWorkingDir: root,
  entryPoints: ["docs/app/main.js"],
  bundle: true,
  format: "esm",
  minify: true,
  target: ["es2020"],
  outfile: "docs/assets/app.bundle.js",
  legalComments: "none",
  logLevel: "info",
});

await build({
  absWorkingDir: root,
  entryPoints: ["docs/assets/styles.css"],
  bundle: true,
  minify: true,
  outfile: "docs/assets/styles.min.css",
  legalComments: "none",
  logLevel: "info",
});
