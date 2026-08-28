export function npmVersionForPython(version) {
    const match = version.match(
        /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:(a|b|rc)(0|[1-9]\d*))?(?:\.dev(0|[1-9]\d*))?$/,
    );
    if (!match) {
        throw new Error(`Unsupported Pytincture release version: ${version}`);
    }
    const base = `${match[1]}.${match[2]}.${match[3]}`;
    if (match[4]) {
        const label = { a: "alpha", b: "beta", rc: "rc" }[match[4]];
        const prerelease = `${base}-${label}.${match[5]}`;
        return match[6] ? `${prerelease}.dev.${match[6]}` : prerelease;
    }
    if (match[6]) {
        return `${base}-dev.${match[6]}`;
    }
    return base;
}
