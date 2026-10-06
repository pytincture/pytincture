# Browser startup lifecycle

`runTinctureApp()` follows a deterministic sequence and resolves only after the
application entrypoint completes. Default legacy Pyodide delivery emits these
outer stages:

1. `preflight`
2. `runtime-load`
3. `package-install`
4. `widgetset-install`
5. `widgetset-load`
6. `archive-download` (packaged applications)
7. `archive-unpack` (packaged applications)
8. `entrypoint-execution`
9. `ready`

Pass `onLifecycleEvent(event)` to integrate a loading UI or diagnostics. The
same event is dispatched on `window` as `pytincture:lifecycle`. Event types are
`stage-start`, `stage-complete`, `stage-failed`, `compatibility`, `fallback`,
`error`, and `ready`. Every event has a `stage`, `requestId`, and ISO `timestamp`.

```javascript
runTinctureApp({
  application: "example",
  widgetlib: "dhxpyt==0.9.18",
  onLifecycleEvent(event) {
    console.info(event.type, event.stage, event.requestId);
  },
});
```

Portable delivery uses bundle download/installation and module-import phases
instead of the legacy package-installation pipeline. Both paths also emit
timed phases for runtime initialization and the first application frame.
Use `pytinctureRuntime.getInfo()` for engine, delivery mode, bundle/profile
identity, and startup timings; see [runtime identity and startup timing](browser-runtimes.md#runtime-identity-and-startup-timing)
for the phase inventory. The first-frame marker does not prove application-specific
data loading or rendering has finished.

The legacy compatibility event reports the Pytincture runtime version, Pyodide and
Python versions, installed widgetset version, verified asset-manifest source,
loaded JavaScript/CSS asset counts, and whether DHTMLX exposed `window.dhx`
when `dhxpyt` is used.

The package-install stages accept exact package/version pins or SHA-256-locked
wheel URLs and disable automatic transitive dependency installation. During
`widgetset-load`, only files in the installed widget distribution's explicit
asset manifest are loaded, and each file is SHA-256 verified before use.

## Errors and fallback

Startup rejects with `PytinctureLifecycleError`. Its public fields are:

- `stage` and `code` for programmatic handling;
- `resource` with query strings removed;
- `requestId` for the browser startup attempt;
- `correlationId` when a backend response supplied `X-Request-ID`; and
- `rootCause`, a bounded diagnostic with common credentials redacted.

The runtime never includes response bodies in lifecycle errors. Application
authors should likewise avoid placing secrets in exception messages.

In legacy delivery, automatic packaged-to-inline fallback occurs only when the
archive endpoint returns HTTP 404 or 410, and emits an explicit `fallback` event. Network
errors, authorization failures, server failures, corrupt archives, and Python
entrypoint exceptions stop startup at their actual stage. `mode: "package"`
never falls back.

Portable startup never silently switches engines or falls back to legacy delivery.
