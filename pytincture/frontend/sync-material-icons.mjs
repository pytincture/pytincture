import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";


const frontendDirectory = path.dirname(fileURLToPath(import.meta.url));
const packageDirectory = path.join(frontendDirectory, "node_modules", "@mdi", "font");
const packageMetadata = JSON.parse(
    await readFile(path.join(packageDirectory, "package.json"), "utf8"),
);
const expectedVersion = "7.4.47";
if (packageMetadata.version !== expectedVersion) {
    throw new Error(`Expected @mdi/font ${expectedVersion}, received ${packageMetadata.version}`);
}

const vendorDirectory = path.join(frontendDirectory, "vendor", "materialdesignicons");
const fontDirectory = path.join(vendorDirectory, "fonts");
await mkdir(fontDirectory, { recursive: true });

const sourceCss = await readFile(
    path.join(packageDirectory, "css", "materialdesignicons.min.css"),
    "utf8",
);
const localFontFace = '@font-face{font-family:"Material Design Icons";src:url("fonts/materialdesignicons-webfont.woff2?v=7.4.47") format("woff2");font-weight:normal;font-style:normal;font-display:block}';
const vendoredCss = sourceCss.replace(/^@font-face\{[^}]+\}/, localFontFace);
if (vendoredCss === sourceCss) {
    throw new Error("Unable to localize the Material Design Icons font URL");
}

await writeFile(
    path.join(vendorDirectory, "materialdesignicons.css"),
    `${vendoredCss.trim()}\n`,
);
await copyFile(
    path.join(packageDirectory, "css", "materialdesignicons.min.css.map"),
    path.join(vendorDirectory, "materialdesignicons.css.map"),
);
await copyFile(
    path.join(packageDirectory, "fonts", "materialdesignicons-webfont.woff2"),
    path.join(fontDirectory, "materialdesignicons-webfont.woff2"),
);
await copyFile(
    path.join(packageDirectory, "LICENSE"),
    path.join(vendorDirectory, "LICENSE"),
);
