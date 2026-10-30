using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Collections.Generic;
using System.Collections.Immutable;
using System.Reflection;
using System.Reflection.Emit;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;
using System.Reflection.PortableExecutable;
using System.Security.Cryptography;

// Protocol 2 is deliberately separate from the legacy metadata/resource decoder.
// Only framework metadata readers inspect candidate bytes. No dependency loading,
// attribute construction, resource deserialization or candidate invocation occurs.
internal sealed class SecurityAnalysis
{
    const int MaxFile = 64 * 1024 * 1024, MaxOutput = 256 * 1024;
    const int MaxMethods = 50000, MaxReferences = 50000, MaxResources = 16384;
    const int MaxMethodIL = 1024 * 1024, MaxIL = 24 * 1024 * 1024, MaxInstructions = 2000000;
    const int MaxString = 512, MaxEvidence = 64, MaxCalls = 128;
    readonly HashSet<string> reasons = new();
    readonly Dictionary<string, long> counts = new();
    readonly Dictionary<string, long> sizes = new();
    readonly Dictionary<string, long> omitted = new();
    readonly List<object> declarations = new(), calls = new(), pinvokes = new(), strings = new(), resources = new();
    readonly Dictionary<string, long> categories = new(), resourceKinds = new();
    readonly Dictionary<AssemblyReferenceHandle, DeclaringScope> assemblyScopes = new();
    sealed record AssemblyIdentity(string name, string version, string culture, string? public_key_token);
    sealed record DeclaringScope(string kind, string? token, AssemblyIdentity? assembly);
    readonly Dictionary<ushort, OpCode> opcodes = typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static)
        .Where(f => f.FieldType == typeof(OpCode)).Select(f => (OpCode)f.GetValue(null)!)
        .ToDictionary(o => unchecked((ushort)o.Value));
    static readonly string[] CountKeys = { "methods_total", "methods_examined", "methods_with_body", "methods_scanned", "methods_skipped",
        "instructions_scanned", "call_sites_total", "sensitive_calls_total", "member_references_total", "member_references_scanned",
        "sensitive_declarations_total", "pinvokes_total", "strings_scanned", "interesting_strings_total", "resources_total", "resources_scanned",
        "embedded_resources", "external_resources", "suspicious_resources_total", "unresolved_tokens" };

    SecurityAnalysis(long fileBytes)
    {
        foreach (var key in CountKeys) counts[key] = 0;
        foreach (var key in new[] { "declarations", "calls", "pinvokes", "strings", "resources" }) omitted[key] = 0;
        sizes["file_bytes"] = fileBytes; sizes["metadata_bytes"] = 0; sizes["method_il_bytes"] = 0;
        sizes["scanned_il_bytes"] = 0; sizes["managed_resource_directory_bytes"] = 0; sizes["embedded_resource_payload_bytes"] = 0;
    }
    void AddCount(string key, long value = 1) => counts[key] += value;
    static void Tally(Dictionary<string, long> map, string key) => map[key] = map.GetValueOrDefault(key) + 1;
    string Text(string value)
    {
        if (value.Length <= MaxString) return value;
        reasons.Add("metadata_string_truncated"); return value[..MaxString];
    }
    void Add(List<object> list, string name, object value, int limit = MaxEvidence)
    {
        if (list.Count < limit) list.Add(value); else omitted[name]++;
    }
    static string Token(EntityHandle h) => "0x" + MetadataTokens.GetToken(h).ToString("X8");
    static string Token(int value) => "0x" + value.ToString("X8");

    internal static int Run(bool isolation)
    {
        string status; string? error = null; Dictionary<string, object?>? report = null; SecurityAnalysis? analysis = null;
        bool parsingImage = false;
        try
        {
            if (isolation && !Program.NetworkBoundaryProven()) return Write("sandbox_unavailable", null, "network_boundary_unproven");
            using var stdin = Console.OpenStandardInput(); using var request = new MemoryStream();
            var buffer = new byte[4096]; int n;
            while ((n = stdin.Read(buffer)) != 0) { if (request.Length + n > 16384) throw new LimitException("request_bytes"); request.Write(buffer, 0, n); }
            using var document = JsonDocument.Parse(request.ToArray(), new JsonDocumentOptions { MaxDepth = 4 });
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object || root.EnumerateObject().Count() != 2 ||
                !root.TryGetProperty("protocol_version", out var p) || !p.TryGetInt32(out int version) || version != 2 ||
                !root.TryGetProperty("input_path", out var input) || input.ValueKind != JsonValueKind.String)
                throw new ArgumentException();
            string path = input.GetString()!;
            if (!Path.IsPathFullyQualified(path) || path.Length > 4096) throw new ArgumentException();
            parsingImage = true;
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (stream.Length > MaxFile) throw new LimitException("input_bytes");
            using var pe = new PEReader(stream); var headers = pe.PEHeaders;
            if (headers.PEHeader == null) throw new BadImageFormatException();
            analysis = new SecurityAnalysis(stream.Length);
            var cli = headers.CorHeader;
            int? flags = cli == null ? null : (int)cli.Flags;
            report = new() { ["machine"] = headers.CoffHeader.Machine.ToString(), ["cli_flags"] = flags,
                ["any_cpu"] = headers.CoffHeader.Machine == Machine.I386 && flags.HasValue && (flags.Value & 1) != 0 && (flags.Value & 2) == 0,
                ["assembly_name"] = null, ["assembly_version"] = null, ["references"] = Array.Empty<object>() };
            if (!pe.HasMetadata)
            {
                if (cli != null) throw new BadImageFormatException();
                analysis.reasons.Add("native_code_not_analyzed"); status = "native";
            }
            else
            {
                if (cli == null) throw new BadImageFormatException();
                var md = pe.GetMetadataReader(); if (!md.IsAssembly) throw new BadImageFormatException();
                var assembly = md.GetAssemblyDefinition();
                report["assembly_name"] = analysis.Text(md.GetString(assembly.Name)); report["assembly_version"] = assembly.Version.ToString();
                report["references"] = md.AssemblyReferences.Take(256).Select(h => { var r = md.GetAssemblyReference(h); return new { name = analysis.Text(md.GetString(r.Name)), version = r.Version.ToString() }; }).ToArray();
                if (md.AssemblyReferences.Count > 256) analysis.reasons.Add("assembly_reference_limit");
                analysis.sizes["metadata_bytes"] = pe.GetMetadata().Length;
                if (((int)cli.Flags & 1) == 0) analysis.reasons.Add("mixed_native_code_not_analyzed");
                analysis.Scan(md, pe); status = "managed";
            }
            report["security"] = analysis.Report();
        }
        catch (LimitException ex) { status = "limit_exceeded"; error = ex.Message; report = null; }
        catch (Exception ex) when (ex is JsonException || ex is ArgumentException || ex is InvalidOperationException)
        { status = parsingImage ? "corrupt" : "invalid_request"; error = parsingImage ? "invalid_pe_metadata" : "invalid_request"; report = null; }
        catch (Exception ex) when (ex is BadImageFormatException || ex is OverflowException || ex is IndexOutOfRangeException)
        { status = "corrupt"; error = "invalid_pe_metadata"; report = null; }
        catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
        { status = "io_error"; error = "input_unavailable"; report = null; }
        byte[] bytes = Encode(status, report, error);
        if (analysis != null && report != null && bytes.Length > MaxOutput)
        {
            // Evidence truncation never discards totals or turns incomplete IL
            // into complete coverage. Keep a bounded representative prefix.
            foreach (var pair in new[] { ("resources", analysis.resources), ("declarations", analysis.declarations),
                ("calls", analysis.calls), ("strings", analysis.strings), ("pinvokes", analysis.pinvokes) })
            {
                while (pair.Item2.Count > 0 && bytes.Length > MaxOutput)
                {
                    pair.Item2.RemoveAt(pair.Item2.Count - 1); analysis.omitted[pair.Item1]++;
                    report["security"] = analysis.Report(); bytes = Encode(status, report, error);
                }
            }
        }
        if (bytes.Length > MaxOutput) return Write("limit_exceeded", null, "output_bytes");
        using var stdout = Console.OpenStandardOutput(); stdout.Write(bytes);
        return status is "managed" or "native" ? 0 : 2;
    }
    static byte[] Encode(string status, object? report, string? error) => JsonSerializer.SerializeToUtf8Bytes(new { protocol_version = 2, status, report, error });
    static int Write(string status, object? report, string? error) { using var stdout = Console.OpenStandardOutput(); stdout.Write(Encode(status, report, error)); return 2; }
    object Report() => new { schema_version = 2, analyzer = "psmm-static-security-v2",
        coverage = new { status = reasons.Count == 0 ? "complete" : "partial", reasons = reasons.Order().ToArray(),
            limitations = new[] { "static_instruction_presence_not_execution", "no_control_flow_or_dataflow", "no_dependency_loading",
                "no_resource_deserialization_or_unpacking", "no_deobfuscation", "no_runtime_generated_code", "strings_only_from_ldstr" },
            evidence_limited = omitted.Values.Any(n => n > 0), omitted_evidence_counts = omitted,
            limits = new { input_bytes = MaxFile, output_bytes = MaxOutput, methods = MaxMethods, member_references = MaxReferences,
                resources = MaxResources, method_il_bytes = MaxMethodIL, total_il_bytes = MaxIL, instructions = MaxInstructions,
                string_chars = MaxString, evidence_per_list = MaxEvidence, call_evidence = MaxCalls } },
        sizes, counts, category_counts = categories, resource_kind_counts = resourceKinds,
        declarations, calls, pinvokes, strings, resources };

    void Scan(MetadataReader md, PEReader pe)
    {
        counts["member_references_total"] = md.MemberReferences.Count;
        foreach (var h in md.MemberReferences.Take(MaxReferences))
        {
            AddCount("member_references_scanned"); string target = MethodName(md, h); string? category = Category(target);
            if (category != null) { AddCount("sensitive_declarations_total"); Add(declarations, "declarations", new { kind = "member_reference", category, target, token = Token(h), declaring_scope = MethodScope(md, h) }); }
        }
        if (md.MemberReferences.Count > MaxReferences) reasons.Add("member_reference_limit");
        counts["methods_total"] = md.MethodDefinitions.Count;
        foreach (var h in md.MethodDefinitions.Take(MaxMethods))
        {
            AddCount("methods_examined"); var method = md.GetMethodDefinition(h); string name = MethodName(md, h);
            var import = method.GetImport();
            if (!import.Module.IsNil)
            {
                AddCount("pinvokes_total"); string module = Text(md.GetString(md.GetModuleReference(import.Module).Name));
                string entry = Text(md.GetString(import.Name));
                Add(pinvokes, "pinvokes", new { method = name, method_token = Token(h), module, entry_point = entry, flags = (int)import.Attributes });
                AddCount("sensitive_declarations_total");
                Add(declarations, "declarations", new { kind = "pinvoke", category = "native_interop", target = module + "!" + entry, token = Token(h), declaring_scope = MethodScope(md, h) });
            }
            if (method.RelativeVirtualAddress == 0) continue;
            AddCount("methods_with_body");
            if ((method.ImplAttributes & MethodImplAttributes.CodeTypeMask) != MethodImplAttributes.IL)
            { reasons.Add("non_il_method"); AddCount("methods_skipped"); continue; }
            try
            {
                var body = pe.GetMethodBody(method.RelativeVirtualAddress); var il = body.GetILContent();
                sizes["method_il_bytes"] += il.Length;
                if (il.Length > MaxMethodIL || sizes["scanned_il_bytes"] + il.Length > MaxIL || counts["instructions_scanned"] >= MaxInstructions)
                { reasons.Add("il_scan_limit"); AddCount("methods_skipped"); continue; }
                ScanIL(md, h, name, il); sizes["scanned_il_bytes"] += il.Length; AddCount("methods_scanned");
            }
            catch (Exception ex) when (ex is BadImageFormatException || ex is ArgumentException || ex is OverflowException || ex is IndexOutOfRangeException)
            { reasons.Add("invalid_method_il"); AddCount("methods_skipped"); }
        }
        if (md.MethodDefinitions.Count > MaxMethods) reasons.Add("method_limit");
        ScanResources(md, pe);
    }

    void ScanIL(MetadataReader md, MethodDefinitionHandle method, string name, ImmutableArray<byte> il)
    {
        int position = 0;
        while (position < il.Length)
        {
            if (counts["instructions_scanned"] >= MaxInstructions) { reasons.Add("instruction_limit"); throw new BadImageFormatException(); }
            int offset = position; ushort code = il[position++];
            if (code == 0xfe) { Need(il, position, 1); code = (ushort)(0xfe00 | il[position++]); }
            if (!opcodes.TryGetValue(code, out var op)) throw new BadImageFormatException();
            AddCount("instructions_scanned"); int width = op.OperandType switch {
                OperandType.InlineNone => 0, OperandType.ShortInlineBrTarget or OperandType.ShortInlineI or OperandType.ShortInlineVar => 1,
                OperandType.InlineVar => 2, OperandType.InlineI8 or OperandType.InlineR => 8,
                OperandType.InlineSwitch => -1, _ => 4 };
            if (width == -1) { int cases = Int32(il, position); if (cases < 0 || cases > (il.Length - position - 4) / 4) throw new BadImageFormatException(); width = checked(4 + cases * 4); }
            Need(il, position, width);
            if (op == OpCodes.Ldstr)
            {
                AddCount("strings_scanned"); int token = Int32(il, position);
                if ((token & unchecked((int)0xff000000)) != 0x70000000) throw new BadImageFormatException();
                string value = md.GetUserString(MetadataTokens.UserStringHandle(token & 0xffffff));
                string? kind = StringKind(value);
                if (kind != null) { AddCount("interesting_strings_total"); Add(strings, "strings", new { method = name, method_token = Token(method), offset, kind, value = Text(value) }); }
            }
            if (op == OpCodes.Call || op == OpCodes.Callvirt || op == OpCodes.Newobj || op == OpCodes.Jmp || op == OpCodes.Calli)
            {
                AddCount("call_sites_total"); int token = Int32(il, position); string target; string? category;
                if (op == OpCodes.Calli)
                {
                    var signature = MetadataTokens.EntityHandle(token);
                    if (signature.Kind != HandleKind.StandaloneSignature ||
                        md.GetBlobReader(md.GetStandaloneSignature((StandaloneSignatureHandle)signature).Signature).Length == 0)
                        throw new BadImageFormatException();
                    target = "indirect_signature:" + Token(token); category = "indirect_call";
                }
                else
                {
                    var targetHandle = MetadataTokens.EntityHandle(token);
                    target = MethodName(md, targetHandle); category = Category(target);
                    if (targetHandle.Kind == HandleKind.MethodDefinition && !md.GetMethodDefinition((MethodDefinitionHandle)targetHandle).GetImport().Module.IsNil)
                        category = "native_interop";
                }
                if (category != null)
                {
                    AddCount("sensitive_calls_total"); Tally(categories, category);
                    var targetHandle = MetadataTokens.EntityHandle(token);
                    string targetKind = op == OpCodes.Calli ? "indirect" : targetHandle.Kind switch {
                        HandleKind.MethodDefinition => "method_definition", HandleKind.MemberReference => "member_reference",
                        HandleKind.MethodSpecification => "method_specification", _ => "unresolved" };
                    var scope = op == OpCodes.Calli ? new DeclaringScope("indirect", Token(token), null) : MethodScope(md, targetHandle);
                    Add(calls, "calls", new { method = name, method_token = Token(method), offset, opcode = op.Name, target,
                        target_token = Token(token), target_kind = targetKind, declaring_scope = scope, category }, MaxCalls);
                }
            }
            position += width;
        }
    }
    static void Need(ImmutableArray<byte> bytes, int position, int length) { if (position < 0 || length < 0 || length > bytes.Length - position) throw new BadImageFormatException(); }
    static int Int32(ImmutableArray<byte> bytes, int position) { Need(bytes, position, 4); return bytes[position] | bytes[position + 1] << 8 | bytes[position + 2] << 16 | bytes[position + 3] << 24; }

    string MethodName(MetadataReader md, EntityHandle h, int depth = 0)
    {
        if (depth > 16) throw new BadImageFormatException();
        if (h.Kind == HandleKind.MethodSpecification) return MethodName(md, md.GetMethodSpecification((MethodSpecificationHandle)h).Method, depth + 1);
        if (h.Kind == HandleKind.MethodDefinition) { var m = md.GetMethodDefinition((MethodDefinitionHandle)h); return Text(TypeName(md, m.GetDeclaringType()) + "::" + md.GetString(m.Name)); }
        if (h.Kind == HandleKind.MemberReference) { var m = md.GetMemberReference((MemberReferenceHandle)h); return Text(TypeName(md, m.Parent) + "::" + md.GetString(m.Name)); }
        AddCount("unresolved_tokens"); reasons.Add("unresolved_method_token"); return "unresolved:" + Token(h);
    }
    DeclaringScope UnknownScope(EntityHandle h = default)
    {
        return new DeclaringScope("unresolved", h.IsNil ? null : Token(h), null);
    }
    DeclaringScope MethodScope(MetadataReader md, EntityHandle h, int depth = 0)
    {
        if (depth > 16) throw new BadImageFormatException();
        if (h.Kind == HandleKind.MethodSpecification) return MethodScope(md, md.GetMethodSpecification((MethodSpecificationHandle)h).Method, depth + 1);
        var scope = h.Kind switch {
            HandleKind.MethodDefinition => TypeScope(md, md.GetMethodDefinition((MethodDefinitionHandle)h).GetDeclaringType(), depth + 1),
            HandleKind.MemberReference => TypeScope(md, md.GetMemberReference((MemberReferenceHandle)h).Parent, depth + 1),
            _ => UnknownScope(h) };
        if (scope.kind == "unresolved") reasons.Add("unresolved_declaring_scope");
        return scope;
    }
    DeclaringScope TypeScope(MetadataReader md, EntityHandle h, int depth = 0)
    {
        if (depth > 16) throw new BadImageFormatException();
        if (h.Kind is HandleKind.TypeDefinition or HandleKind.ModuleDefinition) return new DeclaringScope("local", Token(h), null);
        if (h.Kind == HandleKind.MethodDefinition) return MethodScope(md, h, depth + 1); // vararg MemberRef parent
        if (h.Kind == HandleKind.ModuleReference) return new DeclaringScope("module_reference", Token(h), null);
        if (h.Kind == HandleKind.TypeReference) return TypeScope(md, md.GetTypeReference((TypeReferenceHandle)h).ResolutionScope, depth + 1);
        if (h.Kind == HandleKind.TypeSpecification) return md.GetTypeSpecification((TypeSpecificationHandle)h).DecodeSignature(new Scopes(this), depth + 1);
        if (h.Kind == HandleKind.AssemblyReference)
        {
            var handle = (AssemblyReferenceHandle)h;
            if (assemblyScopes.TryGetValue(handle, out var cached)) return cached;
            var reference = md.GetAssemblyReference(handle);
            var blob = md.GetBlobReader(reference.PublicKeyOrToken);
            if (blob.Length > 16384) throw new BadImageFormatException();
            byte[] key = blob.ReadBytes(blob.Length); string? publicToken = null;
            if (key.Length != 0)
            {
                if ((reference.Flags & AssemblyFlags.PublicKey) != 0)
                {
                    // The assembly identity token is the reversed final eight
                    // SHA-1 bytes of a declared full public key; not signature verification.
                    byte[] digest = SHA1.HashData(key); publicToken = Convert.ToHexString(digest[^8..].Reverse().ToArray()).ToLowerInvariant();
                }
                else { if (key.Length != 8) throw new BadImageFormatException(); publicToken = Convert.ToHexString(key).ToLowerInvariant(); }
            }
            var identity = new AssemblyIdentity(Text(md.GetString(reference.Name)), reference.Version.ToString(),
                Text(md.GetString(reference.Culture)), publicToken);
            return assemblyScopes[handle] = new DeclaringScope("assembly_reference", Token(h), identity);
        }
        return UnknownScope(h);
    }
    sealed class Scopes : ISignatureTypeProvider<DeclaringScope, int>
    {
        readonly SecurityAnalysis owner; internal Scopes(SecurityAnalysis owner) { this.owner = owner; }
        public DeclaringScope GetPrimitiveType(PrimitiveTypeCode code) => owner.UnknownScope();
        public DeclaringScope GetTypeFromDefinition(MetadataReader r, TypeDefinitionHandle h, byte raw) => owner.TypeScope(r, h);
        public DeclaringScope GetTypeFromReference(MetadataReader r, TypeReferenceHandle h, byte raw) => owner.TypeScope(r, h);
        public DeclaringScope GetTypeFromSpecification(MetadataReader r, int context, TypeSpecificationHandle h, byte raw) => owner.TypeScope(r, h, context + 1);
        public DeclaringScope GetSZArrayType(DeclaringScope t) => t;
        public DeclaringScope GetArrayType(DeclaringScope t, ArrayShape shape) => t;
        public DeclaringScope GetByReferenceType(DeclaringScope t) => t;
        public DeclaringScope GetPointerType(DeclaringScope t) => t;
        public DeclaringScope GetPinnedType(DeclaringScope t) => t;
        public DeclaringScope GetModifiedType(DeclaringScope modifier, DeclaringScope t, bool required) => t;
        public DeclaringScope GetGenericInstantiation(DeclaringScope t, ImmutableArray<DeclaringScope> arguments) => t;
        public DeclaringScope GetGenericMethodParameter(int context, int index) => owner.UnknownScope();
        public DeclaringScope GetGenericTypeParameter(int context, int index) => owner.UnknownScope();
        public DeclaringScope GetFunctionPointerType(MethodSignature<DeclaringScope> signature) => owner.UnknownScope();
    }
    string TypeName(MetadataReader md, EntityHandle h, int depth = 0)
    {
        if (depth > 16) throw new BadImageFormatException();
        if (h.Kind == HandleKind.TypeDefinition)
        {
            var t = md.GetTypeDefinition((TypeDefinitionHandle)h); var parent = t.GetDeclaringType();
            return Text((parent.IsNil ? md.GetString(t.Namespace) : TypeName(md, parent, depth + 1)) + "." + md.GetString(t.Name));
        }
        if (h.Kind == HandleKind.TypeReference) { var t = md.GetTypeReference((TypeReferenceHandle)h); return Text(md.GetString(t.Namespace) + "." + md.GetString(t.Name)); }
        if (h.Kind == HandleKind.TypeSpecification) return Text(md.GetTypeSpecification((TypeSpecificationHandle)h).DecodeSignature(new Names(this), depth + 1));
        return h.Kind.ToString();
    }
    sealed class Names : ISignatureTypeProvider<string, int>
    {
        readonly SecurityAnalysis owner; internal Names(SecurityAnalysis owner) { this.owner = owner; }
        public string GetPrimitiveType(PrimitiveTypeCode code) => code.ToString();
        public string GetTypeFromDefinition(MetadataReader r, TypeDefinitionHandle h, byte raw) => owner.TypeName(r, h);
        public string GetTypeFromReference(MetadataReader r, TypeReferenceHandle h, byte raw) => owner.TypeName(r, h);
        public string GetTypeFromSpecification(MetadataReader r, int context, TypeSpecificationHandle h, byte raw) => owner.TypeName(r, h, context + 1);
        public string GetSZArrayType(string t) => t + "[]";
        public string GetArrayType(string t, ArrayShape shape) => t + "[]";
        public string GetByReferenceType(string t) => t + "&";
        public string GetPointerType(string t) => t + "*";
        public string GetPinnedType(string t) => t;
        public string GetModifiedType(string modifier, string t, bool required) => t;
        public string GetGenericInstantiation(string t, ImmutableArray<string> arguments) => t;
        public string GetGenericMethodParameter(int context, int index) => "!!" + index;
        public string GetGenericTypeParameter(int context, int index) => "!" + index;
        public string GetFunctionPointerType(MethodSignature<string> signature) => "function_pointer";
    }

    static string? Category(string target)
    {
        if (target.StartsWith("System.Reflection.Assembly::Load", StringComparison.Ordinal) || target.Contains("System.Runtime.Loader.AssemblyLoadContext::Load") || target.Contains("System.AppDomain::Load")) return "dynamic_load";
        if (target.StartsWith("System.Reflection.Emit.") || target.Contains("::CompileAssemblyFrom") || target.Contains("::CompileToMethod")) return "dynamic_code";
        if (target.StartsWith("System.Diagnostics.Process::Start") || target.StartsWith("System.Diagnostics.ProcessStartInfo::")) return "process_start";
        if (target.StartsWith("System.Net.") || target.StartsWith("UnityEngine.Networking.") || target.Contains("UnityEngine.WWW::.ctor")) return "network";
        if (target.StartsWith("System.Runtime.InteropServices.NativeLibrary::") || target.Contains("Marshal::GetDelegateForFunctionPointer")) return "native_interop";
        if (target.StartsWith("Microsoft.Win32.Registry")) return "registry";
        if (target.Contains("ProtectedData::Unprotect") || target.Contains("PasswordVault::")) return "credential_access";
        if (target.StartsWith("System.IO.File::") || target.StartsWith("System.IO.Directory::") || target.StartsWith("System.IO.FileInfo::") || target.StartsWith("System.IO.DirectoryInfo::"))
        {
            if (target.Contains("::Delete")) return "file_delete";
            if (target.Contains("::Write") || target.Contains("::Append") || target.Contains("::Create") || target.Contains("::Move") || target.Contains("::Copy")) return "file_write";
            if (target.Contains("::Read") || target.Contains("::Get") || target.Contains("::Enumerate") || target.Contains("::Exists")) return "file_read";
            return "filesystem"; // Open/attribute setters may write; do not infer access mode.
        }
        if (target.StartsWith("System.IO.FileStream::") || target.StartsWith("System.IO.StreamWriter::") || target.StartsWith("System.IO.StreamReader::")) return "filesystem";
        if (target.StartsWith("System.Reflection.") && (target.Contains("::Invoke") || target.Contains("::GetMethod") || target.Contains("::GetField") || target.Contains("::GetType") || target.Contains("::SetValue"))) return "reflection";
        if (target.Contains("UnityEngine.Application::OpenURL")) return "open_url";
        return null;
    }
    static string? StringKind(string value)
    {
        if (value.Contains("://", StringComparison.Ordinal)) return "url";
        string text = value.ToLowerInvariant().Replace('\\', '/');
        if (new[] { "login data", "local state", "loginusers.vdf", "cookies", "wallet", "credentials", "/.ssh", "authorized_keys", "id_rsa", "browser/", "password" }.Any(text.Contains)) return "sensitive_path";
        if (new[] { "currentversion/run", "start menu/programs/startup", "schtasks", "powershell", "cmd.exe", "wscript", "mshta", "rundll32" }.Any(text.Contains)) return "command_or_persistence";
        if (text.Contains("appdata") || text.Contains("userprofile") || text.Contains("programdata") || text.Contains(":/") || text.StartsWith("//")) return "path";
        return null;
    }

    void ScanResources(MetadataReader md, PEReader pe)
    {
        var directory = pe.PEHeaders.CorHeader!.ResourcesDirectory;
        sizes["managed_resource_directory_bytes"] = directory.Size; counts["resources_total"] = md.ManifestResources.Count;
        var ordinary = new List<object>(); int ordinaryTotal = 0;
        var extents = new List<(long Start, long End)>();
        foreach (var h in md.ManifestResources.Take(MaxResources))
        {
            var r = md.GetManifestResource(h); string name = Text(md.GetString(r.Name));
            if (!r.Implementation.IsNil) { AddCount("resources_scanned"); AddCount("external_resources"); reasons.Add("external_resource_not_read"); Add(resources, "resources", new { name, embedded = false, offset = (long)r.Offset, size = (int?)null, header_kind = "external" }); continue; }
            AddCount("embedded_resources");
            try
            {
                if (r.Offset > int.MaxValue || directory.Size < 4 || r.Offset > directory.Size - 4) throw new BadImageFormatException();
                var reader = pe.GetSectionData(directory.RelativeVirtualAddress).GetReader((int)r.Offset, directory.Size - (int)r.Offset);
                int size = reader.ReadInt32(); if (size < 0 || size > reader.RemainingBytes) throw new BadImageFormatException();
                sizes["embedded_resource_payload_bytes"] += size;
                extents.Add(((long)r.Offset, (long)r.Offset + 4 + size));
                string kind = Header(reader.ReadBytes(Math.Min(size, 16))); Tally(resourceKinds, kind); AddCount("resources_scanned");
                object row = new { name, embedded = true, offset = (long)r.Offset, size, header_kind = kind };
                if (kind is "pe" or "zip" or "7z" or "rar" or "gzip") { AddCount("suspicious_resources_total"); Add(resources, "resources", row); }
                else { ordinaryTotal++; if (ordinary.Count < MaxEvidence) ordinary.Add(row); }
            }
            catch (Exception ex) when (ex is BadImageFormatException || ex is ArgumentException || ex is OverflowException)
            { reasons.Add("invalid_resource_bounds"); }
        }
        // Keep suspicious headers even when hundreds of ordinary sprites came first.
        int take = Math.Min(MaxEvidence - resources.Count, ordinary.Count); resources.AddRange(ordinary.Take(take)); omitted["resources"] += ordinaryTotal - take;
        long end = -1;
        foreach (var extent in extents.OrderBy(e => e.Start))
        {
            if (extent.Start < end) reasons.Add("overlapping_resource_regions");
            end = Math.Max(end, extent.End);
        }
        if (md.ManifestResources.Count > MaxResources) reasons.Add("resource_scan_limit");
    }
    static string Header(byte[] bytes)
    {
        string hex = Convert.ToHexString(bytes);
        if (hex.StartsWith("4D5A")) return "pe";
        if (hex.StartsWith("504B0304") || hex.StartsWith("504B0506") || hex.StartsWith("504B0708")) return "zip";
        if (hex.StartsWith("377ABCAF271C")) return "7z";
        if (hex.StartsWith("526172211A07")) return "rar";
        if (hex.StartsWith("1F8B")) return "gzip";
        if (hex.StartsWith("89504E470D0A1A0A")) return "png";
        if (hex.StartsWith("FFD8FF")) return "jpeg";
        if (hex.StartsWith("47494638")) return "gif";
        if (hex.StartsWith("52494646")) return "riff";
        if (hex.StartsWith("4F676753")) return "ogg";
        return "other";
    }
}
