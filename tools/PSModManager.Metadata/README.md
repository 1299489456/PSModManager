# Static metadata helper (protocols 1 and 2)

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

For protocol 1, no arguments is accepted solely for self-authored fixture direct
invocation. Protocol 2 has the separate arguments documented below; other
argument lists are rejected. stdin is exactly one UTF-8
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

## Security evidence: separate protocol 2

Use `MetadataWorker(data_root).inspect_security(package_root, relative_file,
cancelled=callback)` from the trusted host. It preserves the legacy result envelope:
`{file_name,relative_path,sha256,size,status,error,metadata,process}`. The SHA-256
binds the result to the staged input bytes; a source change during parsing returns
`changed`, `INPUT_CHANGED`, and `metadata:null`. The legacy `inspect()` and protocol
1 remain unchanged. Missing isolation/runtime, cancellation, crash, timeout,
cleanup failure or invalid output cannot produce usable security metadata.

The parent runs `--require-isolation --security` with
`{"protocol_version":2,"input_path":"<absolute-staged-copy>"}`. The helper keeps
the same four-field response envelope and status/exit-code vocabulary, now with
`protocol_version:2`. `--security` without the isolation flag is only for
self-authored fixture tests. Protocol 1 and 2 requests cannot be interchanged.
The helper performs the same pre-open network-boundary check; candidates are
never loaded into the CLR. `System.Reflection.Emit.OpCodes` reflection is against
the trusted framework opcode table, not against candidate assemblies.

On `managed`/`native`, `metadata` contains
`machine,cli_flags,any_cpu,assembly_name,assembly_version,references,security`.
The `security` object has these stable fields:

- `schema_version:2`, `analyzer:"psmm-static-security-v2"`. Protocol 2 schema 1
  caches must be invalidated: they omitted declaring scopes and a Steam
  credential-path literal. Protocol 1 is unchanged.
- `coverage`: `status:complete|partial`, sorted `reasons`, fixed `limitations`,
  `evidence_limited`, per-list `omitted_evidence_counts`, and numeric `limits`.
- `sizes`: `file_bytes`, `metadata_bytes`, `method_il_bytes`, `scanned_il_bytes`,
  `managed_resource_directory_bytes`, `embedded_resource_payload_bytes`.
- `counts`: `methods_total`, `methods_examined`, `methods_with_body`,
  `methods_scanned`, `methods_skipped`, `instructions_scanned`, `call_sites_total`,
  `sensitive_calls_total`, `member_references_total`, `member_references_scanned`,
  `sensitive_declarations_total`, `pinvokes_total`, `strings_scanned`,
  `interesting_strings_total`, `resources_total`, `resources_scanned`,
  `embedded_resources`, `external_resources`, `suspicious_resources_total`,
  `unresolved_tokens`.
- `category_counts`: sensitive IL instruction totals by category, independent of
  whether their evidence samples fit in the response.
- `resource_kind_counts`: all inspected embedded resource headers by kind.
- `declarations`: `{kind:member_reference|pinvoke,category,target,token,declaring_scope}`.
- `calls`: `{method,method_token,offset,opcode,target,target_token,target_kind,declaring_scope,category}`.
  Offset is zero-based bytes from the method's IL body, not a file offset.
  Opcodes are `call`, `callvirt`, `newobj`, `jmp`, `calli`. Overloads are identified
  by metadata token; display names omit signatures and are not unique identities.
  `target_kind` is `method_definition`, `member_reference`,
  `method_specification`, `indirect`, or `unresolved`.
- `pinvokes`: `{method,method_token,module,entry_point,flags}`.
- `strings`: `{method,method_token,offset,kind,value}` from IL `ldstr` only;
  kind is `url`, `sensitive_path`, `command_or_persistence`, or `path`.
- `resources`: `{name,embedded,offset,size,header_kind}`. Offset is relative to the
  managed resource directory. External resources have null size and are never
  opened. Header kinds: `pe`, `zip`, `7z`, `rar`, `gzip`, `png`, `jpeg`, `gif`,
  `riff`, `ogg`, `other`, `external`. These are prefix hints, not validated files;
  `pe` means an MZ prefix, not a confirmed runnable embedded program.

Categories are `dynamic_load`, `dynamic_code`, `process_start`, `network`,
`native_interop`, `registry`, `credential_access`, `file_delete`, `file_write`,
`file_read`, `filesystem`, `reflection`, `open_url`, `indirect_call`. A metadata
declaration does not prove an IL call exists. An IL call does not prove that it is
reachable or executed. Names are static metadata, not an authenticated library
identity. URLs and paths are candidate-authored literals, never dereferenced.
`loginusers.vdf`, including relative-path spelling, is a sensitive-path literal.

Every call/declaration now carries `declaring_scope:{kind,token,assembly}`.
Kinds are `local`, `assembly_reference`, `module_reference`, `indirect`,
`unresolved`. A local definition remains local even when it declares a name like
`System.IO.File`. An external scope's `token` is the exact AssemblyRef table token;
its `assembly` has `name,version,culture,public_key_token`. Other scope kinds have
`assembly:null`; tokens identify the local TypeDef/ModuleDef, ModuleRef, indirect
signature or unresolved metadata handle as appropriate. MethodSpec and generic
TypeSpec scopes follow their declared base handles, without loading dependencies.
An absent public key token is null. Full public keys are reduced to their
standard SHA-1 identity token within a 16 KiB key limit. These are declared
reference identities, not verified signatures or proof of runtime resolution.
A high-risk framework-call combination must not rely on type-name substrings
alone; match the external assembly name and expected public-key token, preserve
that evidence, and keep unknown/local names out of framework-only conclusions.

`complete` means the bounded static traversals finished, not that the package is
safe or that control/data flow is known. It does not include dependency loading,
deobfuscation, native disassembly, runtime-generated code, full string-heap
search, resource deserialization, or archive unpacking. Plain native PE reports
`native` with `partial` / `native_code_not_analyzed`; mixed managed/native flags
also make coverage partial. Invalid IL, bad resource bounds, external resources,
overlapping resource regions, truncated metadata strings and traversal limits
add explicit partial reasons. Corrupt PE/metadata returns no report.

Evidence arrays are representative samples: 128 calls and 64 entries for each
other list. `evidence_limited:true` by itself does not mean traversal failed, and
consumers must not treat missing sampled calls/strings as proof of absence.
Counts and byte totals cover every successfully inspected row within traversal
limits; PE/archive header samples take precedence over ordinary image samples.
Resource byte counts are logical payload sums, excluding four-byte length
prefixes; overlapping regions are flagged partial and may appear more than once
in that logical sum. IL byte counts likewise sum method bodies, not unique file
regions. `scanned_il_bytes` counts fully decoded method bodies only.

Protocol 2 limits: request 16 KiB, input 64 MiB, response 256 KiB, 50,000 methods,
50,000 member references, 256 assembly references, 16,384 resource rows,
1 MiB IL per method, 24 MiB aggregate decoded IL, two million instructions,
512 UTF-16 code units per displayed string. Resources read only their length and
at most 16 header bytes. The resource count is not limited by the evidence-array
cap. Response overflow drops sample tails with exact omitted counts, never totals;
if a bounded response still cannot be made, it returns `limit_exceeded`.
Existing parent timeout/memory/cleanup limits remain independently enforced.

Run `python -m pytest tests/metadata/test_security_parser.py -q` for protocol 2.
Tests include a nonexecuted static-constructor sentinel, 850 resources with late
MZ/ZIP prefixes, output limits, native/invalid evidence, and real Windows LPAC
worker execution. The compiler uses a caller-owned response file to keep large
resource fixtures below Windows command-line limits. Both development and offline
self-contained builds compile `SecurityAnalysis.cs`; `helper-build.json` records
its separate `security_source_sha256` provenance alongside the legacy source hash.
