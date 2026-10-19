# Static metadata helper (protocol 1)

Build offline with the already installed .NET 10 SDK/reference pack:

```powershell
.venv/Scripts/python.exe tools/PSModManager.Metadata/build.py
# Test/alternate artifact root:
.venv/Scripts/python.exe tools/PSModManager.Metadata/build.py --output-dir <isolated-directory>
```

The development route has no NuGet restore, dependency installation or SDK download. It
selects the highest installed net10 SDK/reference pack and writes a deterministic
assembly plus runtimeconfig to `bin/` (or the explicit output directory).

For the Windows x64 portable product, build a self-contained directory with a
new, caller-owned output path outside this source directory:

```powershell
.venv/Scripts/python.exe tools/PSModManager.Metadata/build.py --self-contained --output-dir <new-helper-directory>
```

This route requires the installed .NET 10 SDK and its matching reference/host
packs, plus the matching `Microsoft.NETCore.App.Runtime.win-x64` nupkg already
present in the local NuGet cache (`NUGET_PACKAGES`, or the standard user cache).
Missing material fails before creating output; it is never downloaded. The
builder copies the cached nupkg into an owned local feed, restores from that
feed with `NuGetAudit=false`, then publishes with `--no-restore`. Transitive
framework prefetch is disabled because this helper references only NETCore.App;
targeting/host pack download is also disabled. No online package source is used.

The helper is a normal x64 apphost with its adjacent .NET runtime, not a trimmed
or single-file bundle. This keeps runtime loading compatible with the existing
AppContainer sandbox and avoids extraction into a shared temporary directory.
All project, CLI, NuGet and intermediate state stays in the output's `.build/`
tree. Successful builds safely remove that owned tree; failures retain it.
`helper-build.log` records commands/exit codes, and `helper-build.json` records
SDK/runtime versions and input/package hashes. The directory also carries .NET
license and third-party notices. Invoke `PSModManager.Metadata.exe
--require-isolation` inside the same product sandbox; end-user machines need no
system .NET installation for this helper. Portable GUI hosts must also package
the trusted console launcher rather than assume a development `python.exe`.

Invoke `dotnet <directory>/PSModManager.Metadata.dll --require-isolation` in the
product sandbox. The product parent always supplies this flag; it is not a user
setting. Before opening input (and before reading the request), the helper tries
to construct IPv4 and IPv6 TCP sockets without connecting anywhere. Both must
fail with Windows access-denied/provider-denied errors 10013 or 10107. Successful
construction or any other SocketException (including unsupported IPv6) returns
`sandbox_unavailable`, `report:null`, `error:"network_boundary_unproven"`, exit 2.
Timeout or connection refusal never establishes the boundary. This probe is one
fail-closed check in addition to the parent's sandbox and access boundary checks;
it is not a claim that a generic child process provides isolation.

No arguments is accepted solely for self-authored fixture direct invocation;
all other argument lists are rejected. stdin is exactly one UTF-8
JSON document followed by EOF:

```json
{"protocol_version":1,"input_path":"E:\\isolated-copy\\input.dll"}
```

Only these two keys are accepted. The host supplies an isolated, read-only copy;
the helper's path validation alone is **not an isolation boundary**. Direct
invocation is for self-authored fixtures. Untrusted production input must go
through the application's sandbox and capability checks.

stdout contains exactly one JSON object, no diagnostic logging or absolute error
paths: `{protocol_version,status,report,error}`. `status` is `managed`, `native`,
`corrupt`, `limit_exceeded`, `invalid_request`, `io_error` or `sandbox_unavailable`. Successful managed or
native classification exits 0; the other outcomes exit 2. Unexpected process
failure remains a parent-observed crash, never successful parsing.

Managed report fields: `machine`, numeric `cli_flags`, `any_cpu`, `assembly_name`,
`assembly_version`, `references` (name/version), `custom_attributes`
(type/decoded/arguments/named_arguments), `resources`
(name/embedded/size/sha256/json_text). Native report uses null identity/flags and
empty collections. AnyCPU requires I386, ILOnly and no Requires32Bit; numeric
flags preserve Prefers32Bit and all other distinctions.

PEReader/MetadataReader only inspect bytes. No Assembly.Load, dependency loading,
reflection invocation, attribute construction, module initialization or resource
execution occurs. MelonInfo/MelonGame argument strings retain original versions.
External enum decoding is deliberately unsupported (attribute `decoded:false`)
rather than opening an untrusted dependency to infer its underlying type. Invalid
attribute blobs have an explicit per-attribute error. External resources are
listed `embedded:false` without opening their target. Invalid UTF-8 or malformed
JSON resources retain hash/size with `json_text:null`.

Hard helper limits: request 16 KiB; input 64 MiB; output 256 KiB; assembly references
256; assembly attributes 256; attribute blob 64 KiB; metadata string 8192 UTF-16
code units; resources 64; each embedded resource 256 KiB, aggregate 1 MiB;
JSON depth 32. Output overflow returns a small `limit_exceeded` response. Parent
enforces process time, memory, stdout/stderr and subtree cleanup independently.

Run `python -m pytest tests/metadata/test_static_parser.py -q` with the repository
venv. Tests compile into pytest's isolated temporary root, call the real helper,
and never execute input fixtures. Existing PS-00-05 fixture sources are retained.
The native fixture is a self-authored PE with the CLI directory removed; it is a
classification fixture, not a runnable native application. .NET API behavior is
documented by the installed reference pack's `System.Reflection.Metadata.xml`.
