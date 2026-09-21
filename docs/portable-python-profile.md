# Portable Python profile 2

Identifier: `pytincture-portable-2`. The supported targets are the framework's
pinned Pyodide 0.29.3 (CPython) and MicroPython WebAssembly package 1.29.0-6.
This profile defines a browser build target, not general CPython emulation.

Profile 2 returns UUID objects from both `uuid.uuid4()` and `from uuid import
uuid4`, matching CPython's value API. Profile 1's direct-import transformation
returned strings. Rebuild to opt into profile 2 and use `str(uuid4())` where text
is required. The updated browser loader still accepts existing profile-1 bundles;
it does not rewrite their embedded code. Legacy Pyodide is unchanged.

## Independent targets

The builder analyzes both targets independently. `runtimes` selects which targets
must succeed and which source sets are published. `compatibility.json` reports
supported/unsupported status, source counts, native module inventory, transformations,
behavior-changing transformations, injected shims, assets, required CSP origins,
errors and static-analysis limitations for each target.

Pyodide preserves CPython annotations, dataclasses and standard-library APIs. It
removes conventional `__main__` and `TYPE_CHECKING` guards and replaces BFF modules
with client stubs, as required by browser delivery. Explicit import substitutions
and widget-scoped asset bridges apply to both targets and are reported. It does not apply MicroPython
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
| `create_once_callable` | Callable JS wrapper with one invocation or explicit `.destroy()` cancellation; releases its callback reference even if invocation throws |
| `pyodide.ffi.wrappers` | `add_event_listener` and `remove_event_listener` retain callbacks until removal, then release their JS bridge references |
| MainWindow/Layout subclass initialization | Framework/widget lifecycle initializes nested layouts after constructors |
| Dictionary unpacking and selected class keywords | Rewritten to supported forms; custom metaclasses are rejected |
| List, tuple and set display unpacking | Expanded with helpers that preserve expression evaluation and iterator-consumption order |
| `os.getenv`, `from os import getenv`, `os.environ.get` | Browser-local environment initialized empty; defaults and browser-assigned values work, and build/server environment values are never copied |
| `time.monotonic`, including import aliases | Browser `performance.now()` divided by 1000; elapsed seconds independent of wall-clock adjustments |
| Dataclasses | Portable helper supports fields, factories, inheritance, post-init, repr/equality, init, kw-only, asdict/fields/replace/is_dataclass; frozen/slots/order/hash/InitVar rejected |
| String title casing | Portable implementation; Unicode title rules can differ from CPython |
| `uuid` | `UUID` values and cryptographic browser `uuid4`; string, hex, bytes, bytes_le, fields and integer construction; no host identity/time-based UUID generation |
| Exception formatting and inspect helpers | Limited browser diagnostics; no arbitrary stack/source/signature reflection |
| Bare `except:` | Catches `BaseException`, including interrupts; generated exception bindings are distinct for nested traceback formatting |
| `from pathlib import Path` | Optional POSIX path subset over the browser filesystem; supported methods and limits below |
| `from html import escape` | Optional shim escapes ampersands, angle brackets and (by default) both quote characters; `quote=False` preserves quotes |
| `html.unescape`, `html.entities` | Pinned CPython 3.13 HTML5 entities and character-reference replacement rules, with a MicroPython-compatible scanner |
| `html.parser.HTMLParser` | Incremental tag/attribute, text, comment, declaration, entity and raw script/style callbacks, `feed`, `close`, `reset`, `getpos`, `get_starttag_text`; no DOM construction or nesting repair |
| `string` | ASCII character constants and the upstream `translate` helper; `Template` and `Formatter` are not provided |
| `base64` | Base16/32/64 encoding and decoding, standard and URL-safe Base64; reported extension fills upstream URL-safe decoding and error-alias gaps |
| `contextlib` | Synchronous `contextmanager`, `closing`, `suppress`, `nullcontext`; async contexts and `ExitStack` are not provided |
| `csv` | Text `reader`/`writer`, Excel/Excel-tab/Unix dialects, quoting, delimiters and escapes; no dialect registration or dictionary helpers |
| `zipfile` | Pinned read-only stored/deflated ZIP support, with compressed-stream bound fix; no archive writing, encryption, extraction, ZIP64 or CRC verification |
| Task scheduling | Browser scheduler handles callbacks/awaitables; no threads |
| `asyncio.get_running_loop()` | Scheduling view with `is_running()` and `create_task()` only; not a full asyncio loop |
| Resources | Verified in-memory `files`, joinpath, name/suffix, is_file/is_dir, iterdir, read_bytes and UTF-8 read_text (string package names); no filesystem writes or arbitrary encodings |
| Logging | Lightweight browser output helper; no full CPython handler/configuration system |

The report names actual changed AST rules (for example `adapt-Call` or
`adapt-ClassDef`) rather than claiming that every construct in this table changed.
Python helper modules are listed in `shims`; optional `copy`, `types`, `datetime`, `string`, `base64` and `zipfile`
come from hash-verified pinned MicroPython standard-library sources. These modules
also have upstream limitations. Review the corresponding generated source for an
exact transformation. This profile's version must change when its promised
semantics change incompatibly.

Optional modules and their dependencies are included only when imported or
declared in the dynamic-import allowlist; all are listed in the report. Pinned
vendor sources remain hash-checked before reported extensions are applied, and
bundles carry their upstream licenses. The framework's `pathlib` and `html` shims
are included only when imported
and listed in the compatibility report. Pyodide continues using its native
standard library and native `create_once_callable` implementation.

Portable `Path` supports string/Path construction, `/`, `joinpath`, `name`, `stem`,
`suffix`, `suffixes`, `parts`, `parent`, `parents`, `with_name`, `with_suffix`,
`as_posix`, `cwd`, `is_absolute`, `absolute`, `resolve`, `relative_to`, `stat`,
`exists`, `is_file`, `is_dir`, `iterdir`, `open`, `read_text`, `read_bytes`,
`write_text`, `write_bytes`, `mkdir`, `unlink`, and `rmdir`. Resolution is lexical
over the browser's POSIX paths; `strict=True` also checks existence. `stat` exposes
the ten basic fields, without nanosecond or platform-specific extensions. Text
methods support UTF-8. Use Path's IO methods (or explicit `str(path)` with native
MicroPython IO); OS-wide path-protocol support is not implied.

Path suffix semantics follow the browser's CPython 3.13 reference: for `Path("a/b.")`,
`suffix == ""`, `suffixes == []`, and `stem == "b."`. Python 3.14 changed this
behavior; running a build or its tests on a 3.14 host does not change the target.

Verified bundle resources are installed into the in-memory filesystem on both
engines, so `Path(__file__).resolve().parent / "data.json"` can read a declared
resource. Writes remain local to that browser runtime and are not persisted or
sent to the server. Windows paths, symlink resolution, globbing, home expansion,
permissions and pathlib APIs beyond those listed are outside this
subset. Unavailable methods are not silently emulated.

`from pyodide.code import run_js` (including import aliases) is supported in
MicroPython. Like [Pyodide's API](https://pyodide.org/en/0.29.3/usage/api/python-api/code.html#pyodide.code.run_js),
it accepts a positional JavaScript string, returns the browser result, rejects
non-strings, and raises `RuntimeError` with the JavaScript error text when evaluation
fails, so Python handlers can catch it in MicroPython. Native Pyodide application
imports remain native. This does not add other `pyodide.code` APIs or change CSP;
JavaScript evaluation remains subject to the application's browser policy.

## Build failures and explicit requirements

Build-time checks reject unresolved imports, native extension wheels, unsupported
syntax (including match/exception groups for MicroPython), custom reflection,
undeclared dynamic imports, unsupported dataclass options, source escape paths,
stale widget asset hashes, missing package/CSS resources and oversized inputs.
Nested f-strings inside replacement expressions (including PEP 701 nesting with
reused quotes) are lowered for MicroPython into ordinary formatting calls and
string joins. The application source does not need rewriting. Expressions stay
in place: conditional branches remain lazy, values are evaluated once in field
order, and `await` retains its scope. Conversion (`!s`, `!r`, `!a`) happens before
format-spec evaluation, matching the pinned CPython 3.13 reference. Custom
`__format__` methods run after their spec is evaluated; non-string results and
formatting exceptions propagate. Built-in formatting uses MicroPython's formatter,
with explicit boolean numeric conversion and Unicode ASCII escaping for `!a`.
The build report marks each affected `JoinedStr` as a transformation. Unaffected
f-strings keep their existing path, and Pyodide retains native CPython f-strings.

Known unsupported MicroPython APIs such as `time.perf_counter`
and `asyncio.to_thread` are diagnosed. Other runtime API
differences still need application tests; the checker is not a whole-program proof.

Dynamic imports must be declared in `dynamic-imports` and resolve to a
bundled/native module. With this explicit allowlist, `importlib.import_module(name)`
accepts computed names on both engines. The generated guard checks the resulting
absolute name for an exact match before importing, including for modules already
loaded. An unlisted name raises `ImportError`; allowing a package does not allow
all its children. Computed calls without an allowlist still fail the build.
Normal dependencies imported by an allowed module remain subject to bundle
discovery and profile checks. This guard covers recognized `import_module` calls
and import aliases, not arbitrary reflection or a sandbox for untrusted Python.
Use explicit `import-aliases` to substitute a portable registry module for a
computed/server registry while leaving the application import unchanged. The
selected implementation must itself pass the target profile, and the report lists
every substituted source. Widget JS import adaptation also appears in the report;
it prevents old loaders from executing/inserting manifest-owned assets again.
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
