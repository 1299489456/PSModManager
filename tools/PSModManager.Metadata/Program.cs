using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Collections.Generic;
using System.Collections.Immutable;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Security.Cryptography;
using System.Net.Sockets;

internal sealed class LimitException : Exception { public LimitException(string text) : base(text) {} }
internal static class Program
{
    const int MaxInput = 16 * 1024, MaxOutput = 256 * 1024, MaxFile = 64 * 1024 * 1024;
    static string Text(string value) { if (value.Length > 8192) throw new LimitException("metadata_string"); return value; }
    static int Main(string[] args)
    {
        string status; object? report = null; string? error = null; bool parsingImage = false;
        List<Dictionary<string, object?>>? resourceReports = null;
        Dictionary<string, object?>? resourceCoverage = null;
        try
        {
            if (args.Length != 0 && (args.Length != 1 || args[0] != "--require-isolation")) throw new ArgumentException("arguments");
            if (args.Length == 1 && !NetworkBoundaryProven())
            {
                using var deniedOutput = Console.OpenStandardOutput();
                deniedOutput.Write(JsonSerializer.SerializeToUtf8Bytes(new { protocol_version = 1, status = "sandbox_unavailable", report = (object?)null, error = "network_boundary_unproven" }));
                return 2;
            }
            using var stdin = Console.OpenStandardInput();
            using var request = new MemoryStream(); var buffer = new byte[4096]; int count;
            while ((count = stdin.Read(buffer)) != 0) { if (request.Length + count > MaxInput) throw new LimitException("request_bytes"); request.Write(buffer, 0, count); }
            using var doc = JsonDocument.Parse(request.ToArray(), new JsonDocumentOptions { MaxDepth = 4 });
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object || root.EnumerateObject().Count() != 2 ||
                !root.TryGetProperty("protocol_version", out var protocol) || !protocol.TryGetInt32(out int version) || version != 1 ||
                !root.TryGetProperty("input_path", out var input) || input.ValueKind != JsonValueKind.String)
                throw new ArgumentException("protocol");
            string path = input.GetString()!;
            if (!Path.IsPathFullyQualified(path) || path.Length > 4096) throw new ArgumentException("input_path");
            parsingImage = true;
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (stream.Length > MaxFile) throw new LimitException("input_bytes");
            using var pe = new PEReader(stream);
            var header = pe.PEHeaders;
            if (header.PEHeader == null) throw new BadImageFormatException();
            string machine = header.CoffHeader.Machine.ToString();
            var cli = header.CorHeader;
            if (!pe.HasMetadata)
            { if (cli != null) throw new BadImageFormatException(); status = "native"; report = new { machine, cli_flags = (int?)null, any_cpu = false, assembly_name = (string?)null, assembly_version = (string?)null, references = Array.Empty<object>(), custom_attributes = Array.Empty<object>(), resources = Array.Empty<object>(), resource_coverage = new { status = "complete", total_count = 0, reported_count = 0, omitted_count = 0, skipped_payload_count = 0, output_limited = false } }; }
            else
            {
                if (cli == null) throw new BadImageFormatException();
                var md = pe.GetMetadataReader(); if (!md.IsAssembly) throw new BadImageFormatException();
                var assembly = md.GetAssemblyDefinition();
                if (md.AssemblyReferences.Count > 256 || assembly.GetCustomAttributes().Count > 256) throw new LimitException("metadata_rows");
                var references = md.AssemblyReferences.Select(h => { var r = md.GetAssemblyReference(h); return new { name = Text(md.GetString(r.Name)), version = r.Version.ToString() }; }).ToArray();
                var attributes = new List<object>(); var provider = new AttributeTypes();
                foreach (var h in assembly.GetCustomAttributes())
                {
                    var attr = md.GetCustomAttribute(h);
                    if (md.GetBlobReader(attr.Value).Length > 65536) throw new LimitException("attribute_bytes");
                    string name = AttributeName(md, attr.Constructor);
                    try
                    {
                        var decoded = attr.DecodeValue(provider);
                        attributes.Add(new { type = name, decoded = true,
                            arguments = decoded.FixedArguments.Select(a => Value(a.Value)).ToArray(),
                            named_arguments = decoded.NamedArguments.Select(a => new { name = Text(a.Name ?? ""), value = Value(a.Value) }).ToArray() });
                    }
                    catch (BadImageFormatException) { attributes.Add(new { type = name, decoded = false, error = "invalid_attribute_blob" }); }
                }
                var resources = new List<Dictionary<string, object?>>(); resourceReports = resources;
                long total = 0; int skippedPayloads = 0;
                foreach (var h in md.ManifestResources.Take(64))
                {
                    var r = md.GetManifestResource(h); string name = Text(md.GetString(r.Name));
                    if (!r.Implementation.IsNil) { resources.Add(new() { ["name"] = name, ["embedded"] = false }); continue; }
                    if (r.Offset > int.MaxValue || cli.ResourcesDirectory.Size < 4 || r.Offset > cli.ResourcesDirectory.Size - 4) throw new BadImageFormatException();
                    var block = pe.GetSectionData(cli.ResourcesDirectory.RelativeVirtualAddress);
                    var reader = block.GetReader((int)r.Offset, cli.ResourcesDirectory.Size - (int)r.Offset);
                    int length = reader.ReadInt32();
                    if (length < 0 || length > reader.RemainingBytes) throw new BadImageFormatException();
                    if (length > 256 * 1024 || total + length > 1024 * 1024)
                    {
                        resources.Add(new() { ["name"] = name, ["embedded"] = true, ["size"] = length,
                            ["sha256"] = null, ["json_text"] = null, ["status"] = "skipped_limit",
                            ["reason"] = length > 256 * 1024 ? "resource_bytes" : "resource_total_bytes" });
                        skippedPayloads++; continue;
                    }
                    total += length;
                    byte[] bytes = reader.ReadBytes(length); string? json = null;
                    if (name.EndsWith(".json", StringComparison.OrdinalIgnoreCase))
                    {
                        try { string value = new UTF8Encoding(false, true).GetString(bytes); using var parsed = JsonDocument.Parse(value, new JsonDocumentOptions { MaxDepth = 32 }); json = value; }
                        catch (Exception ex) when (ex is JsonException || ex is DecoderFallbackException) { }
                    }
                    resources.Add(new() { ["name"] = name, ["embedded"] = true, ["size"] = length,
                        ["sha256"] = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant(), ["json_text"] = json });
                }
                resourceCoverage = new() { ["status"] = skippedPayloads > 0 || md.ManifestResources.Count > resources.Count ? "partial" : "complete",
                    ["total_count"] = md.ManifestResources.Count, ["reported_count"] = resources.Count,
                    ["omitted_count"] = md.ManifestResources.Count - resources.Count, ["skipped_payload_count"] = skippedPayloads,
                    ["output_limited"] = false };
                int flags = (int)cli.Flags;
                report = new { machine, cli_flags = flags, any_cpu = header.CoffHeader.Machine == Machine.I386 && (flags & 1) != 0 && (flags & 2) == 0,
                    assembly_name = Text(md.GetString(assembly.Name)), assembly_version = assembly.Version.ToString(), references, custom_attributes = attributes, resources, resource_coverage = resourceCoverage };
                status = "managed";
            }
        }
        catch (LimitException ex) { status = "limit_exceeded"; error = ex.Message; }
        catch (Exception ex) when (ex is JsonException || ex is ArgumentException || ex is InvalidOperationException) { status = parsingImage ? "corrupt" : "invalid_request"; error = parsingImage ? "invalid_pe_metadata" : "invalid_request"; }
        catch (Exception ex) when (ex is BadImageFormatException || ex is OverflowException || ex is IndexOutOfRangeException) { status = "corrupt"; error = "invalid_pe_metadata"; }
        catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException) { status = "io_error"; error = "input_unavailable"; }
        byte[] output = JsonSerializer.SerializeToUtf8Bytes(new { protocol_version = 1, status, report, error });
        if (output.Length > MaxOutput && status == "managed" && resourceReports != null && resourceReports.Count > 0 && resourceCoverage != null)
        {
            // Optional resource output must not discard already bounded assembly identity.
            resourceReports.Clear(); resourceCoverage["status"] = "partial";
            resourceCoverage["reported_count"] = 0; resourceCoverage["omitted_count"] = resourceCoverage["total_count"];
            resourceCoverage["output_limited"] = true;
            output = JsonSerializer.SerializeToUtf8Bytes(new { protocol_version = 1, status, report, error });
        }
        if (output.Length > MaxOutput) { status = "limit_exceeded"; output = JsonSerializer.SerializeToUtf8Bytes(new { protocol_version = 1, status, report = (object?)null, error = "output_bytes" }); }
        using var stdout = Console.OpenStandardOutput(); stdout.Write(output);
        return status == "managed" || status == "native" ? 0 : 2;
    }
    static bool NetworkBoundaryProven()
    {
        bool denied = true;
        foreach (var family in new[] { AddressFamily.InterNetwork, AddressFamily.InterNetworkV6 })
        {
            try { using var socket = new Socket(family, SocketType.Stream, ProtocolType.Tcp); denied = false; }
            catch (SocketException ex) { if (ex.NativeErrorCode != 10013 && ex.NativeErrorCode != 10107) denied = false; }
        }
        return denied;
    }
    static object? Value(object? value) => value switch
    {
        string s => Text(s),
        ImmutableArray<CustomAttributeTypedArgument<string>> a => a.Select(x => Value(x.Value)).ToArray(),
        _ => value
    };
    static string TypeName(MetadataReader md, EntityHandle handle) => handle.Kind switch
    {
        HandleKind.TypeReference => Text(md.GetString(md.GetTypeReference((TypeReferenceHandle)handle).Namespace) + "." + md.GetString(md.GetTypeReference((TypeReferenceHandle)handle).Name)),
        HandleKind.TypeDefinition => Text(md.GetString(md.GetTypeDefinition((TypeDefinitionHandle)handle).Namespace) + "." + md.GetString(md.GetTypeDefinition((TypeDefinitionHandle)handle).Name)),
        _ => "unknown"
    };
    static string AttributeName(MetadataReader md, EntityHandle ctor) => ctor.Kind switch
    {
        HandleKind.MemberReference => TypeName(md, md.GetMemberReference((MemberReferenceHandle)ctor).Parent),
        HandleKind.MethodDefinition => TypeName(md, md.GetMethodDefinition((MethodDefinitionHandle)ctor).GetDeclaringType()),
        _ => "unknown"
    };
    sealed class AttributeTypes : ICustomAttributeTypeProvider<string>
    {
        public string GetPrimitiveType(PrimitiveTypeCode code) => code.ToString();
        public string GetSystemType() => "System.Type";
        public bool IsSystemType(string type) => type == "System.Type";
        public string GetSZArrayType(string type) => type + "[]";
        public string GetTypeFromDefinition(MetadataReader r, TypeDefinitionHandle h, byte raw) => TypeName(r, h);
        public string GetTypeFromReference(MetadataReader r, TypeReferenceHandle h, byte raw) => TypeName(r, h);
        public string GetTypeFromSerializedName(string name) => Text(name);
        // Resolving external enum definitions would require opening untrusted dependencies.
        public PrimitiveTypeCode GetUnderlyingEnumType(string type) => throw new BadImageFormatException("Unsupported enum");
    }
}
