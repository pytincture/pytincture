# Portable Python profile 1

Identifier: `pytincture-portable-1`. The supported targets are the framework's
pinned Pyodide 0.29.3 (CPython) and MicroPython WebAssembly package 1.29.0-6.
This profile defines a browser build target, not general CPython emulation.

## Independent targets

The builder analyzes both targets independently. `runtimes` selects which targets
must succeed and which source sets are published. `compatibility.json` reports
supported/unsupported status, source counts, native module inventory, transformations,
behavior-changing transformations, injected shims, assets, required CSP origins,
errors and static-analysis limitations for each target.

Pyodide preserves CPython annotations, dataclasses and standard-library APIs. It
removes conventional `__main__` and `TYPE_CHECKING` guards and replaces BFF modules
with client stubs, as required by browser delivery. It does not apply MicroPython
transforms. Native extensions needing browser installation still use legacy
package delivery; portable wheels must contain compatible Python and data.

MicroPython's module inventory is explicit and pinned. The Pyodide inventory comes
from the shipped standard-library archive and linked modules. Presence in that
inventory does not guarantee that operating-system functionality works in a browser.
Pure Python dependency wheels are validated recursively; installing a dependency
on the server does not automatically make it available to portable clients.

## Reported MicroPython adaptations

Every changed AST construct is recorded with file, line, rule and whether it can
change behavior. Reports are deterministic and omit source bodies. Review these
findings and run application conformance before enabling the target.

| Construct | Portable behavior / boundary |
|---|---|
| Annotations and typing-only imports | Removed from runtime evaluation; introspection of annotations is unavailable |
| `create_proxy`, `to_js`, `to_py` | Browser callback bridge; JSON-compatible data conversion, not arbitrary object conversion |
| MainWindow/Layout subclass initialization | Framework/widget lifecycle initializes nested layouts after constructors |
| Dictionary unpacking and selected class keywords | Rewritten to supported forms; custom metaclasses are rejected |
| Dataclasses | Portable helper supports fields, factories, inheritance, post-init, repr/equality, init, kw-only, asdict/fields/replace/is_dataclass; frozen/slots/order/hash/InitVar rejected |
| String title casing | Portable implementation; Unicode title rules can differ from CPython |
| UUID helpers | Browser-generated UUID strings through the compatibility helper |
| Exception formatting and inspect helpers | Limited browser diagnostics; no arbitrary stack/source/signature reflection |
| Task scheduling | Browser scheduler handles callbacks/awaitables; no threads |
| `asyncio.get_running_loop()` | Scheduling view with `is_running()` and `create_task()` only; not a full asyncio loop |
| Resources | Verified in-memory `files`, joinpath, name/suffix, is_file/is_dir, iterdir, read_bytes and UTF-8 read_text (string package names); no filesystem writes or arbitrary encodings |
| Logging | Lightweight browser output helper; no full CPython handler/configuration system |

The report names actual changed AST rules (for example `adapt-Call` or
`adapt-ClassDef`) rather than claiming that every construct in this table changed.
Python helper modules are listed in `shims`; optional `copy`, `types` and `datetime`
come from hash-verified pinned MicroPython standard-library sources. These modules
also have upstream limitations. Review the corresponding generated source for an
exact transformation. This profile's version must change when its promised
semantics change incompatibly.

## Build failures and explicit requirements

Build-time checks reject unresolved imports, native extension wheels, unsupported
syntax (including match/exception groups and iterable display unpacking for MicroPython), custom reflection,
computed dynamic imports, unsupported dataclass options, source escape paths,
stale widget asset hashes, missing package/CSS resources and oversized inputs.
Known unsupported MicroPython APIs such as `time.perf_counter`, `time.monotonic`,
`asyncio.to_thread` and environment lookups are diagnosed. Other runtime API
differences still need application tests; the checker is not a whole-program proof.

Literal dynamic imports must be declared in `dynamic-imports` and resolve to a
bundled/native module. Computed module names are outside the static profile.
Additional modules may be listed in `files`; that alone does not authorize arbitrary
dynamic import behavior. Computed JavaScript asset URLs and reflective Python
patterns cannot all be determined statically and remain a documented test boundary.

Declare required browser API paths in `required-browser-apis`; the loader checks
presence before app startup. API presence does not grant microphone, clipboard,
GPU or other browser permissions. Those flows need browser tests and appropriate
server permissions policy.

Limits are 256 source files / 8 MiB total source, 32 MiB per resource and 128 MiB
aggregate bundle, with at most 4096 inventory entries. Wheel expansion is bounded
before reading. MicroPython heap configuration is 1–128 MiB (16 MiB default).
Size failures do not replace the published bundle.

## BFF behavior

Stubs preserve named arguments, keyword-only/positional-only parameters, server
defaults, variadic parameters and parameterless GET methods. Existing sessions,
authorization and CSRF remain enforced. Normal methods retain synchronous calls;
prefer the generated `method_async` API to avoid blocking the browser. Streaming
stubs expose async iterators; call `aclose()` when stopping early. Async JSON calls
have a 35-second timeout; streams run until completion or explicit cancellation.
External token methods are not converted to session stubs. BFF replay-token mode
currently requires legacy delivery.

Use portable Pyodide when you need CPython behavior without browser-time package
installation. Use MicroPython when the app fits this profile and its conformance
results justify the smaller runtime. Arbitrary CPython libraries remain outside
that guarantee.
