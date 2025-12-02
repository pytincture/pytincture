import { readFile, writeFile } from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, "..", "..");

const pythonVersionFile = path.join(repoRoot, "pytincture", "__init__.py");
const packageJsonFile = path.join(__dirname, "package.json");

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
    if (packageJson.version === version) {
        console.log(`package.json already at ${version}`);
        return;
    }
    packageJson.version = version;
    await writeFile(packageJsonFile, `${JSON.stringify(packageJson, null, 2)}\n`);
    console.log(`Synced package.json version to ${version}`);
}

syncVersion().catch(error => {
    console.error("Failed to sync package version:", error);
    process.exitCode = 1;
});
