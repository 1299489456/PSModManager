using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Runtime.Loader;
using System.Text.Json;

// Execute only the installed game's scanner and its fixed dependencies. Candidate
// DLLs are inputs to its DseCecil metadata reader, never CLR load candidates.
internal static class ApprovalAnalysis
{
    internal static readonly string[] GameLibraries = { "Assembly-CSharp", "DseCecil", "0Harmony", "UnityEngine.CoreModule" };
    static readonly HashSet<string> Capabilities = new(new[] { "files", "network", "process", "dynamic-code", "native", "reflection-external" });
    const int MaxOutput = 256 * 1024;

    sealed class GameContext : AssemblyLoadContext
    {
        readonly string root;
        readonly HashSet<string> framework;
        internal GameContext(string root) : base("current-game-scanner", isCollectible: true)
        {
            this.root = root;
            string runtime = Path.GetFullPath(RuntimeEnvironment.GetRuntimeDirectory()).TrimEnd(Path.DirectorySeparatorChar);
            framework = ((string?)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES") ?? "").Split(Path.PathSeparator)
                .Where(p => string.Equals(Path.GetDirectoryName(p)?.TrimEnd(Path.DirectorySeparatorChar), runtime, StringComparison.OrdinalIgnoreCase))
                .Select(p => Path.GetFileNameWithoutExtension(p)).ToHashSet(StringComparer.OrdinalIgnoreCase);
        }
        protected override Assembly? Load(AssemblyName name)
        {
            if (GameLibraries.Contains(name.Name, StringComparer.OrdinalIgnoreCase))
                return LoadFromAssemblyPath(Path.Combine(root, name.Name + ".dll"));
            if (name.Name != null && framework.Contains(name.Name))
                return Default.LoadFromAssemblyName(name);
            // No default app-directory or candidate-directory resolution.
            throw new FileNotFoundException("scanner_dependency_not_allowed");
        }
        protected override IntPtr LoadUnmanagedDll(string name) => throw new DllNotFoundException("scanner_native_call_not_allowed");
    }

    static string Text(object? value, bool nullable = false)
    {
        if (nullable && value == null) return null!;
        if (value is not string text || text.Length > 4096) throw new LimitException("scanner_output_shape");
        return text;
    }
    static object? Field(object value, string name) => value.GetType().GetField(name, BindingFlags.Instance | BindingFlags.Public)?.GetValue(value);
    static List<object> Findings(object? value, bool hard, int limit)
    {
        if (value is not IEnumerable items) throw new InvalidOperationException();
        var rows = new List<object>();
        foreach (object row in items)
        {
            if (rows.Count >= limit || Field(row, "IsHardFlag") is not bool actual || actual != hard) throw new LimitException("scanner_output_shape");
            rows.Add(new { kind = Text(Field(row, "Kind")), location = Text(Field(row, "Location")), target = Text(Field(row, "Target")), is_hard_flag = hard });
        }
        return rows;
    }

    internal static int Run()
    {
        string status = "io_error", error = "scanner_unavailable"; object? report = null;
        try
        {
            // Approval never has an ordinary-process test/fallback mode.
            if (!Program.NetworkBoundaryProven()) return Write("sandbox_unavailable", null, "network_boundary_unproven");
            using var input = Console.OpenStandardInput(); using var request = new MemoryStream();
            var buffer = new byte[4096]; int count;
            while ((count = input.Read(buffer)) != 0)
            { if (request.Length + count > 16384) throw new LimitException("request_bytes"); request.Write(buffer, 0, count); }
            using var document = JsonDocument.Parse(request.ToArray(), new JsonDocumentOptions { MaxDepth = 3 });
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object || root.EnumerateObject().Count() != 2 ||
                !root.TryGetProperty("protocol_version", out var protocol) || !protocol.TryGetInt32(out int version) || version != 3 ||
                !root.TryGetProperty("assemblies", out var values) || values.ValueKind != JsonValueKind.Array || values.GetArrayLength() is < 1 or > 256)
                throw new ArgumentException();
            string gameRoot = Path.Combine(AppContext.BaseDirectory, "game"), candidateRoot = Path.Combine(AppContext.BaseDirectory, "candidate");
            var paths = new List<string>(); var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (var value in values.EnumerateArray())
            {
                string name = value.GetString() ?? "";
                if (name.Length is < 1 or > 512 || name.Contains('\\') || Path.IsPathRooted(name) || !name.EndsWith(".dll", StringComparison.OrdinalIgnoreCase) ||
                    name.Split('/').Any(p => p.Length == 0 || p is "." or ".." || p.TrimEnd(' ', '.') != p || p.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0) || !names.Add(name))
                    throw new ArgumentException();
                string path = Path.GetFullPath(Path.Combine(candidateRoot, name));
                if (!path.StartsWith(candidateRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new ArgumentException();
                paths.Add(path);
            }
            var context = new GameContext(gameRoot);
            try
            {
                var game = context.LoadFromAssemblyPath(Path.Combine(gameRoot, "Assembly-CSharp.dll"));
                // Awake/Unity Application are deliberately not called. These are
                // the same Managed-tree names collected by the player, not Mod
                // names nor the helper's newer framework assembly inventory.
                var gameNames = Directory.GetFiles(gameRoot, "*.dll", SearchOption.AllDirectories)
                    .Select(p => Path.GetFileNameWithoutExtension(p)).ToHashSet(StringComparer.OrdinalIgnoreCase);
                if (gameNames.Count > 2048) throw new LimitException("game_assembly_count");
                var loader = game.GetType("ModLoader", throwOnError: true)!;
                var namesField = loader.GetField("gameAssemblyNames", BindingFlags.Static | BindingFlags.NonPublic);
                if (namesField?.FieldType != typeof(HashSet<string>)) throw new InvalidOperationException();
                namesField.SetValue(null, gameNames);
                var scanner = game.GetType("ModScanner", throwOnError: true)!;
                var method = scanner.GetMethod("Scan", BindingFlags.Static | BindingFlags.Public, new[] { typeof(IEnumerable<string>), typeof(IEnumerable<string>) });
                if (method == null || method.ReturnType.FullName != "ModScanResult") throw new InvalidOperationException();
                object result = method.Invoke(null, new object?[] { paths, null }) ?? throw new InvalidOperationException();
                if (Field(result, "Capabilities") is not IEnumerable rawCapabilities) throw new InvalidOperationException();
                var caps = rawCapabilities.Cast<object>().Select(c => Text(c)).ToArray();
                if (caps.Length > 6 || caps.Distinct().Count() != caps.Length || caps.Any(c => !Capabilities.Contains(c))) throw new InvalidOperationException();
                var findings = Findings(Field(result, "Findings"), false, 400);
                var hardFlags = Findings(Field(result, "HardFlags"), true, 100);
                string? scanError = Text(Field(result, "Error"), nullable: true);
                if (Field(result, "MethodsScanned") is not int methods || methods < 0 ||
                    result.GetType().GetProperty("Refused")?.GetValue(result) is not bool refused || refused != (scanError != null || hardFlags.Count > 0)) throw new InvalidOperationException();
                var loaded = context.Assemblies.Select(a => a.GetName().Name!).Order().ToArray();
                if (loaded.Any(n => !GameLibraries.Contains(n)) || AppDomain.CurrentDomain.GetAssemblies().Any(a =>
                    !a.IsDynamic && a.Location.StartsWith(candidateRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))) throw new InvalidOperationException();
                report = new { schema_version = 1, scanner = "current-game-ModScanner.Scan", capabilities = caps.Order().ToArray(),
                    findings, hard_flags = hardFlags, error = scanError, refused, methods_scanned = methods,
                    loaded_game_libraries = loaded, candidate_loaded = false };
                status = "managed"; error = null!;
            }
            finally { context.Unload(); }
        }
        catch (LimitException ex) { status = "limit_exceeded"; error = ex.Message; }
        catch (Exception ex) when (ex is JsonException || ex is ArgumentException) { status = "invalid_request"; error = "approval_request_invalid"; }
        catch (Exception) { status = "io_error"; error = "game_scanner_unavailable_or_incompatible"; }
        return Write(status, report, error);
    }
    static int Write(string status, object? report, string? error)
    {
        byte[] output = JsonSerializer.SerializeToUtf8Bytes(new { protocol_version = 3, status, report, error });
        if (output.Length > MaxOutput)
        { status = "limit_exceeded"; output = JsonSerializer.SerializeToUtf8Bytes(new { protocol_version = 3, status, report = (object?)null, error = "output_bytes" }); }
        using var stdout = Console.OpenStandardOutput(); stdout.Write(output);
        return status == "managed" ? 0 : 2;
    }
}
