import assert from "node:assert/strict";
import test from "node:test";

import { npmVersionForPython } from "../versioning.mjs";


test("maps stable and PEP 440 prereleases to npm SemVer", () => {
    assert.equal(npmVersionForPython("1.0.0"), "1.0.0");
    assert.equal(npmVersionForPython("1.0.0rc1"), "1.0.0-rc.1");
    assert.equal(npmVersionForPython("1.0.0a2"), "1.0.0-alpha.2");
    assert.equal(npmVersionForPython("1.0.0b3"), "1.0.0-beta.3");
    assert.equal(npmVersionForPython("1.0.0rc1.dev2"), "1.0.0-rc.1.dev.2");
    assert.equal(npmVersionForPython("1.1.0.dev4"), "1.1.0-dev.4");
});

test("rejects versions that cannot be represented by the release policy", () => {
    assert.throws(() => npmVersionForPython("1.0"), /Unsupported/);
    assert.throws(() => npmVersionForPython("1.0.0.post1"), /Unsupported/);
});
