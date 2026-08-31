import { copyFile, mkdir, readFile, writeFile } from "fs/promises";
import { createHash } from "crypto";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const packageRoot = path.join(__dirname, "node_modules", "swagger-ui-dist");
const outputRoot = path.join(__dirname, "vendor", "swagger-ui");
const repositoryRoot = path.resolve(__dirname, "..", "..");
const manifestPath = path.join(repositoryRoot, "security", "swagger-ui-assets.json");
const version = "5.32.14";
const files = ["LICENSE", "swagger-ui-bundle.js", "swagger-ui.css"];

async function sha256(filePath) {
    const content = await readFile(filePath);
    return createHash("sha256").update(content).digest("hex");
}

await mkdir(outputRoot, { recursive: true });
const assets = [];
for (const name of files) {
    const source = path.join(packageRoot, name);
    const destination = path.join(outputRoot, name);
    await copyFile(source, destination);
    assets.push({
        path: path.relative(repositoryRoot, destination).replaceAll(path.sep, "/"),
        sha256: await sha256(destination),
    });
}

const manifest = {
    schema: 1,
    package: "swagger-ui-dist",
    version,
    license: "Apache-2.0",
    source: `https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-${version}.tgz`,
    npm_integrity: "sha512-nOA2pSQhcmODMUQZpJHYKNuwniDUqcOWGNaSCOoZv12FdOSJ9JxV95HtyRGNMqEBj6h6lCNTy20TgZDYTSuUIg==",
    assets,
};
await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
