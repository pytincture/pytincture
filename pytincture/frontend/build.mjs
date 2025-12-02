import { build } from "esbuild";
import { mkdir } from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const entryPoint = path.resolve(__dirname, "pytincture.js");
const distDir = path.resolve(__dirname, "dist");
const isWatch = process.argv.includes("--watch");

const builds = [
    {
        outfile: path.join(distDir, "pytincture.js"),
        format: "iife",
        minify: false,
        globalName: "PytinctureRuntime",
        banner: { js: "/* pytincture runtime */" },
    },
    {
        outfile: path.join(distDir, "pytincture.min.js"),
        format: "iife",
        minify: true,
        globalName: "PytinctureRuntime",
        banner: { js: "/* pytincture runtime */" },
    },
    {
        outfile: path.join(distDir, "pytincture.esm.js"),
        format: "esm",
        minify: false,
    },
];

const commonOptions = {
    entryPoints: [entryPoint],
    bundle: true,
    target: "es2019",
    platform: "browser",
    sourcemap: true,
};

async function ensureDist() {
    await mkdir(distDir, { recursive: true });
}

function logResult(kind, outfile) {
    const label = kind === "watch" ? "Watching" : "Built";
    console.log(`${label}: ${path.relative(process.cwd(), outfile)}`);
}

async function buildAll() {
    await ensureDist();
    await Promise.all(
        builds.map(async options => {
            await build({
                ...commonOptions,
                ...options,
            });
            logResult("build", options.outfile);
        }),
    );
}

async function watchAll() {
    await ensureDist();
    await Promise.all(
        builds.map(async options => {
            await build({
                ...commonOptions,
                ...options,
                watch: {
                    onRebuild(error) {
                        if (error) {
                            console.error("Rebuild failed:", error);
                        } else {
                            logResult("watch", options.outfile);
                        }
                    },
                },
            });
            logResult("watch", options.outfile);
        }),
    );
}

try {
    if (isWatch) {
        await watchAll();
    } else {
        await buildAll();
    }
} catch (error) {
    console.error("Build failed:", error);
    process.exitCode = 1;
}
