"""Pure static rules for reading legacy evidence, never loader installation/execution.

Formal-release consumers may retain historical inventory/graph interpretation
without importing or shipping the retired loader integration. These rules do not
describe the native Mono mod loader and must not drive native package decisions.

Source basis: LavaGang/MelonLoader tag v0.7.3, read 2026-09-28:
https://raw.githubusercontent.com/LavaGang/MelonLoader/v0.7.3/MelonLoader/Melons/MelonFolderHandler.cs
  FindSubFolders/AddFolder/IsNameExcluded: existence-only manifest gate on
  immediate children; recursive descendants inherit admission and stage.
https://raw.githubusercontent.com/LavaGang/MelonLoader/v0.7.3/MelonLoader/Melons/MelonPreprocessor.cs
  PreprocessFolder: managed DLLs; MelonInfo for Mods/Plugins; per-stage,
  ordinal AssemblyName and highest System.Version. Equal versions keep first
  enumeration, whose unspecified order cannot be inferred from sorted paths.

These predict the stock rules only. Unknown options (including custom runtime
exclusions) degrade to unknown. No prediction is evidence of initialization.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

RULE_VERSION = "melonloader-0.7.3-inventory-v1"
MANIFEST_NAME = "manifest.json"
OPTION_KEYS = frozenset({"DisableSubFolderLoad", "DisableSubFolderManifest"})
STAGES = {"UserLibs": 0, "Plugins": 1, "Mods": 2}


def supports_version(value: str | None) -> bool:
    """Release build metadata does not change 0.7.3; prereleases stay unknown.

    Keep the original PE version in fingerprints/policy for provenance. Do not
    trim arbitrary suffixes: e.g. 0.7.3-experimental is a different rule scope.
    """
    return isinstance(value, str) and len(value) <= 256 and re.fullmatch(
        r'v?0\.7\.3(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?', value) is not None


def manifest_parse(data: bytes) -> dict:
    """Observe JSON quality separately: stock Loader only tests file existence."""
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError):
        return {"present": True, "status": "invalid", "reason": "manifest_json_invalid"}
    return {"present": True, "status": "present", "value": value}


def _result(status: str, stage: str | None, *reasons: str) -> dict:
    return {"status": status, "stage": stage, "reasons": list(reasons),
            "rule_version": RULE_VERSION}


def predict(relative_path: str, *, loader_version: str | None,
            options: dict | None, manifests: dict) -> dict:
    """Predict stock directory admission; manifests maps directory to observation.

    Missing manifest key means confirmed absent. Unreadable/uncertain paths must
    have an explicit observation with present=None; malformed JSON with a known
    existing regular file still satisfies the Loader existence test.
    """
    parts = relative_path.replace("\\", "/").split("/")
    roots = {name.casefold(): name for name in STAGES}
    if (len(parts) < 2 or parts[0].casefold() not in roots or
            any(not p or p in {".", ".."} or ":" in p for p in parts)):
        return _result("unknown", None, "outside_known_loader_roots")
    stage = roots[parts[0].casefold()]
    if not supports_version(loader_version):
        return _result("unknown", stage, "loader_version_unknown_or_unsupported")
    if (not isinstance(options, dict) or set(options) != OPTION_KEYS or
            any(type(v) is not bool for v in options.values())):
        return _result("unknown", stage, "loader_options_unknown_or_unsupported")
    if not parts[-1].lower().endswith(".dll"):
        return _result("excluded", stage, "not_dll")
    directories = parts[1:-1]
    if directories and options["DisableSubFolderLoad"]:
        return _result("excluded", stage, "subfolder_load_disabled")
    reasons = ["stock_directory_rule"]
    for index, name in enumerate(directories):
        if name in {"Broken", "Retired", "Disabled"} or name.startswith((".", "~")):
            return _result("excluded", stage, "exact_directory_exclusion")
        if index == 0 and not options["DisableSubFolderManifest"]:
            observation = manifests.get("/".join(parts[:2]))
            if observation is None or observation.get("present") is False:
                return _result("excluded", stage, "first_subfolder_manifest_absent")
            if observation.get("present") is not True:
                return _result("unknown", stage, "first_subfolder_manifest_unknown")
            reasons.append("manifest_exists_content_not_used_by_loader")
        if name in STAGES and STAGES[name] <= STAGES[stage]:
            stage = name
    return _result("candidate", stage, *reasons)


def _version(value) -> tuple | None:
    if not isinstance(value, str) or len(value) > 43 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value):
        return None
    items = tuple(int(part) for part in value.split("."))
    if any(part > 2147483647 for part in items):
        return None
    return items + (-1,) * (4 - len(items))


def choose_candidates(entries: list[dict], loader_version: str | None) -> list[dict]:
    """Copy entries; annotate prediction and shadowed_by (relative path list).

    Input fields: relative_path, prediction, managed (bool|None), has_melon_info
    (bool|None), assembly_name and assembly_version. Only eligible metadata is
    grouped. Unknown metadata keeps uncertainty; equal maxima have no winner.
    """
    result = deepcopy(entries)
    groups: dict[tuple, list[dict]] = {}
    uncertain_names: set[tuple] = set()
    uncertain_stages: set[str] = set()
    for entry in result:
        prediction = entry["prediction"]
        if not supports_version(loader_version):
            entry["prediction"] = _result("unknown", prediction.get("stage"),
                                          "loader_version_unknown_or_unsupported")
            continue
        if prediction["status"] != "candidate":
            continue
        stage = prediction["stage"]
        if entry.get("managed") is False:
            entry["prediction"] = _result("excluded", stage, "native_not_managed")
            continue
        if stage != "UserLibs" and entry.get("has_melon_info") is False:
            entry["prediction"] = _result("excluded", stage, "melon_info_absent")
            continue
        if (entry.get("managed") is not True or
                (stage != "UserLibs" and entry.get("has_melon_info") is not True) or
                not entry.get("assembly_name")):
            entry["prediction"] = _result("unknown", stage, "assembly_metadata_unknown")
            if entry.get("assembly_name"):
                uncertain_names.add((stage, entry["assembly_name"]))
            else:
                uncertain_stages.add(stage)
            continue
        groups.setdefault((stage, entry["assembly_name"]), []).append(entry)
    for (stage, name), group in groups.items():
        versions = [_version(entry.get("assembly_version")) for entry in group]
        if None in versions:
            for entry in group:
                entry["prediction"] = _result("unknown", stage, "assembly_version_unknown")
            continue
        maximum = max(versions)
        winners = [entry for entry, version in zip(group, versions) if version == maximum]
        for entry, version in zip(group, versions):
            if version < maximum:
                entry["prediction"] = _result("excluded", stage, "assembly_version_shadowed")
                entry["shadowed_by"] = [winner["relative_path"] for winner in winners]
            elif len(winners) > 1:
                entry["prediction"] = _result("unknown", stage, "equal_version_enumeration_order_unknown")
            elif stage in uncertain_stages or (stage, name) in uncertain_names:
                entry["prediction"] = _result("unknown", stage, "other_assembly_metadata_unknown")
            else:
                entry["prediction"]["reasons"].append("highest_known_assembly_version_candidate")
    return result
