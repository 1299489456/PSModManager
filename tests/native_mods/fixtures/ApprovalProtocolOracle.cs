// Independent inert protocol oracle for Steam build 26284217.
// Based on ModLoader.ParseManifestCore, ModFingerprint.Compute/FileEntries and
// ModTrust.CompareFiles. Reads fixture bytes; never loads any candidate assembly.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Xml.Linq;

static class ApprovalProtocolOracle
{
    static string HashFile(string path)
    {
        using var input = File.OpenRead(path);
        using var hash = SHA256.Create();
        return Convert.ToHexString(hash.ComputeHash(input)).ToLowerInvariant();
    }

    static void Main(string[] args)
    {
        string folder = args[0], source = args[2], workshopId = ulong.Parse(args[3], CultureInfo.InvariantCulture).ToString(CultureInfo.InvariantCulture);
        var root = XDocument.Load(Path.Combine(folder, "manifest.xml")).Root!;
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var paths = root.Element("Assemblies")!.Elements("Assembly")
            .Select(e => e.Value.Trim().Replace('\\', '/')).Where(v => v.Length != 0)
            .Where(v => seen.Add(Path.GetFullPath(Path.Combine(folder, v)))).ToArray();
        string manifestHash = HashFile(Path.Combine(folder, "manifest.xml"));
        string[] files = paths.Select(p => p + "|" + (File.Exists(Path.Combine(folder, p)) ? HashFile(Path.Combine(folder, p)) : "missing")).ToArray();
        string combinedHash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(manifestHash + string.Concat(files.Select(f => "\n" + f))))).ToLowerInvariant();
        using var state = JsonDocument.Parse(File.ReadAllText(args[1]));
        using var trust = JsonDocument.Parse(state.RootElement.GetProperty("modTrust").GetProperty("value").GetString()!);
        string id = root.Element("ID")!.Value.Trim();
        var record = trust.RootElement.GetProperty("records").EnumerateArray().First(r => r.GetProperty("id").GetString() == id);
        string verdict = record.GetProperty("source").GetString() != source || record.GetProperty("workshopId").GetString() != workshopId
            ? "Changed" : record.GetProperty("combinedHash").GetString() == combinedHash ? "Match" : "Updated";
        bool dates = DateTime.TryParse(record.GetProperty("approvedAt").GetString(), CultureInfo.InvariantCulture,
            DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out var approved) && approved.Kind == DateTimeKind.Utc;
        bool detected_is_null = record.GetProperty("detected").ValueKind == JsonValueKind.Null;
        Console.Write(JsonSerializer.Serialize(new { manifestHash, files, combinedHash, verdict, dates, detected_is_null }));
    }
}
