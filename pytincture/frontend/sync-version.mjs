import { readFile, writeFile } from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..", "..");

const pythonVersionFile = path.join(repoRoot, "pytincture", "__init__.py");
const packageJsonFile = path.join(__dirname, "package.json");
const runtimeSourceFile = path.join(__dirname, "pytincture.js");

async function readPythonVersion() {
    const content = await readFile(pythonVersionFile, "utf-8");
    const match = content.match(/__version__\s*=\s*["']([^"']+)["']/);
    if (!match) {
        throw new Error(`Could not find __version__ in ${pythonVersionFile}`);
    }
    return match[1];
}

async function syncVersion() {
    const version = await readPythonVersion();
    const packageJson = JSON.parse(await readFile(packageJsonFile, "utf-8"));
    if (packageJson.version !== version) {
        packageJson.version = version;
        await writeFile(packageJsonFile, `${JSON.stringify(packageJson, null, 2)}\n`);
        console.log(`Synced package.json version to ${version}`);
    } else {
        console.log(`package.json already at ${version}`);
    }

    const runtimeSource = await readFile(runtimeSourceFile, "utf-8");
    const updatedRuntimeSource = runtimeSource.replace(
        /const PYTINCTURE_RUNTIME_VERSION = ["'][^"']+["'];/,
        `const PYTINCTURE_RUNTIME_VERSION = ${JSON.stringify(version)};`,
    );
    if (updatedRuntimeSource === runtimeSource) {
        console.log(`pytincture.js already at ${version}`);
    } else {
        await writeFile(runtimeSourceFile, updatedRuntimeSource);
        console.log(`Synced pytincture.js version to ${version}`);
    }
}

syncVersion().catch(error => {
    console.error("Failed to sync package version:", error);
    process.exitCode = 1;
});
