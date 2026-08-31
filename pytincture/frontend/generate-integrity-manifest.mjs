import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";


const frontendDirectory = path.dirname(fileURLToPath(import.meta.url));
const packageMetadata = JSON.parse(
    await readFile(path.join(frontendDirectory, "package.json"), "utf8"),
);
const packageLock = JSON.parse(
    await readFile(path.join(frontendDirectory, "package-lock.json"), "utf8"),
);
const materialIconsPackage = packageLock.packages?.["node_modules/@mdi/font"];
if (
    materialIconsPackage?.version !== "7.4.47"
    || !/^sha512-[A-Za-z0-9+/]+=*$/.test(materialIconsPackage?.integrity || "")
) {
    throw new Error("@mdi/font must be exactly locked with npm integrity metadata");
}
const runtimeSource = await readFile(path.join(frontendDirectory, "pytincture.js"), "utf8");
const versionMatch = runtimeSource.match(
    /^const PYTINCTURE_RUNTIME_VERSION = ["']([^"']+)["'];/m,
);
if (!versionMatch) {
    throw new Error("Pytincture runtime version is missing");
}
const frameworkVersion = versionMatch[1];
const assetPaths = [
    "pytincture.js",
    "sw.js",
    "dist/pytincture.js",
    "dist/pytincture.js.map",
    "dist/pytincture.esm.js",
    "dist/pytincture.esm.js.map",
    "dist/pytincture.min.js",
    "dist/pytincture.min.js.map",
    "vendor/materialdesignicons/LICENSE",
    "vendor/materialdesignicons/materialdesignicons.css",
    "vendor/materialdesignicons/fonts/materialdesignicons-webfont.woff2",
    "pyodide/0.29.3/sbom.json",
    "pyodide/0.29.3/full/micropip-0.11.0-py3-none-any.whl",
    "pyodide/0.29.3/full/micropip-0.11.0-py3-none-any.whl.metadata",
    "pyodide/0.29.3/full/pyodide-lock.json",
    "pyodide/0.29.3/full/pyodide.asm.js",
    "pyodide/0.29.3/full/pyodide.asm.wasm",
    "pyodide/0.29.3/full/pyodide.js",
    "pyodide/0.29.3/full/python_stdlib.zip",
];

const assets = [];
for (const assetPath of assetPaths) {
    const bytes = await readFile(path.join(frontendDirectory, assetPath));
    assets.push({
        path: assetPath,
        sha256: createHash("sha256").update(bytes).digest("hex"),
        sri: `sha384-${createHash("sha384").update(bytes).digest("base64")}`,
    });
}

const integrityDirectory = path.join(frontendDirectory, "integrity");
await mkdir(integrityDirectory, { recursive: true });
for (const entry of await readdir(integrityDirectory)) {
    if (/^pytincture-.+\.json$/.test(entry)) {
        await rm(path.join(integrityDirectory, entry));
    }
}
const manifest = {
    schema: 1,
    framework_version: frameworkVersion,
    npm_version: packageMetadata.version,
    pyodide_version: "0.29.3",
    material_design_icons_version: "7.4.47",
    material_design_icons_source: {
        package: "@mdi/font",
        resolved: materialIconsPackage.resolved,
        npm_integrity: materialIconsPackage.integrity,
    },
    trust_model: "Copy SRI values into the application or verify local bytes before deployment; do not fetch this manifest from the same untrusted asset origin at runtime.",
    assets,
};
const outputPath = path.join(
    integrityDirectory,
    `pytincture-${frameworkVersion}.json`,
);
await writeFile(outputPath, `${JSON.stringify(manifest, null, 2)}\n`);
console.log(`Built: ${path.relative(process.cwd(), outputPath)}`);
