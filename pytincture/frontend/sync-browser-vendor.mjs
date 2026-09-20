import {copyFile, mkdir, readFile, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const destination = path.resolve(root, '../browser_vendor');
await mkdir(destination, {recursive:true});
const packages = [
    ['@micropython/micropython-webassembly-pyscript', '1.29.0-6', {
        'micropython.mjs':'micropython.mjs', 'micropython.wasm':'micropython.wasm',
    }],
    ['@fontsource/material-icons', '5.3.0', {
        'files/material-icons-latin-400-normal.woff2':'material-icons.woff2',
        'LICENSE':'MATERIAL-ICONS-LICENSE',
    }],
];
const inventory = {schema:1, packages:[]};
for (const [name, version, files] of packages) {
    const source = path.join(root, 'node_modules', name);
    const metadata = JSON.parse(await readFile(path.join(source, 'package.json'), 'utf8'));
    if (metadata.version !== version) throw new Error(`Expected ${name}@${version}`);
    const record = {name, version, license:metadata.license, files:{}};
    for (const [input, output] of Object.entries(files)) {
        await copyFile(path.join(source, input), path.join(destination, output));
        record.files[output] = createHash('sha256').update(await readFile(path.join(destination, output))).digest('hex');
    }
    inventory.packages.push(record);
}
await writeFile(path.join(destination, 'inventory.json'), JSON.stringify(inventory, null, 2) + '\n');
