"""Synthetic policy facts: never scan, write or execute a game tree."""
from copy import deepcopy

import pytest

from backend.loaders.melonloader.v073 import choose_candidates, manifest_parse, predict

OPTIONS = {"DisableSubFolderLoad": False, "DisableSubFolderManifest": False}


def policy(path, *, options=None, manifests=None, version="0.7.3"):
    return predict(path, loader_version=version, options=OPTIONS if options is None else options,
                   manifests={} if manifests is None else manifests)


@pytest.mark.parametrize("root", ["Mods", "Plugins", "UserLibs", "mods", "PLUGINS", "userlibs"])
def test_roots_are_windows_categories(root):
    assert policy(root + "/中文 % 文件.DLL")["status"] == "candidate"


@pytest.mark.parametrize("folder", ["Broken", "Retired", "Disabled", ".hidden", "~backup"])
def test_exact_default_exclusions(folder):
    assert policy("Mods/" + folder + "/a.dll", options={**OPTIONS, "DisableSubFolderManifest": True})["status"] == "excluded"


@pytest.mark.parametrize("folder", ["broken", "retired", "disabled", "DISABLED", "Backup"])
def test_similar_names_are_not_reliable_disabled_libraries(folder):
    assert policy("Mods/" + folder + "/a.dll", manifests={"Mods/" + folder: manifest_parse(b"{}")})["status"] == "candidate"


def test_only_first_subfolder_needs_manifest_and_bad_json_is_still_present():
    manifests = {"Mods/中文 100%": manifest_parse(b"not json")}
    assert manifests["Mods/中文 100%"]["status"] == "invalid"
    result = policy("Mods/中文 100%/deeper/a.dll", manifests=manifests)
    assert result["status"] == "candidate"
    assert "manifest_exists_content_not_used_by_loader" in result["reasons"]
    assert policy("Mods/中文 100%/deeper/a.dll")["status"] == "excluded"


def test_unknown_manifest_is_not_absent_or_candidate():
    assert policy("Mods/Sub/a.dll", manifests={"Mods/Sub": {"present": None, "status": "unknown"}})["status"] == "unknown"


def test_options_change_prediction_not_physical_path():
    path = "Mods/sub/deep/a.dll"
    assert policy(path)["status"] == "excluded"
    assert policy(path, options={**OPTIONS, "DisableSubFolderManifest": True})["status"] == "candidate"
    assert policy(path, options={"DisableSubFolderManifest": True, "DisableSubFolderLoad": True})["status"] == "excluded"
    assert policy("Mods/a.dll", options={**OPTIONS, "DisableSubFolderLoad": True})["status"] == "candidate"


@pytest.mark.parametrize("options", [None, {}, {**OPTIONS, "custom": True}, {**OPTIONS, "DisableSubFolderLoad": 0}])
def test_unknown_options_are_conservative(options):
    assert predict("Mods/a.dll", loader_version="0.7.3", options=options, manifests={})["status"] == "unknown"


@pytest.mark.parametrize("version", [None, "0.7.4", "0.7.3-experimental", "", "0.7.3+", "0.7.3+abc..def", "0.7.3+abc/def", "0.7.3-beta+abc"])
def test_unknown_loader_never_uses_certain_v073_rule(version):
    assert policy("Mods/a.dll", version=version)["status"] == "unknown"


@pytest.mark.parametrize('version', ['0.7.3+982ed9981ac5b0619648a2e79bcc02fd8861db33', 'v0.7.3+build.2'])
def test_release_build_metadata_preserves_loader_rules(version):
    assert policy('Mods/a.dll', version=version)['status']=='candidate'
    entries=[assembly('Mods/a.dll','1.0.0.0'),assembly('Mods/b.dll','2.0.0.0')]
    result=choose_candidates(entries,version)
    assert result[0]['prediction']['status']=='excluded'
    assert result[1]['prediction']['status']=='candidate'


@pytest.mark.parametrize("path,stage", [("Mods/a/Plugins/x.dll", "Plugins"), ("Mods/a/UserLibs/x.dll", "UserLibs"), ("UserLibs/a/Mods/x.dll", "UserLibs"), ("Plugins/a/Mods/x.dll", "Plugins"), ("Mods/a/plugins/x.dll", "Mods")])
def test_nested_stage_can_only_move_earlier_with_exact_name(path, stage):
    assert policy(path, options={**OPTIONS, "DisableSubFolderManifest": True})["stage"] == stage


def assembly(path, version, *, name="Same", managed=True, melon=True):
    return {"relative_path": path, "prediction": policy(path), "managed": managed,
            "has_melon_info": melon, "assembly_name": name, "assembly_version": version}


def test_highest_version_shadows_but_keeps_all_physical_entries_and_input():
    entries = [assembly("Mods/a.dll", "1.0.0.0"), assembly("Mods/z.dll", "2.0.0.0")]
    original = deepcopy(entries)
    result = choose_candidates(entries, "0.7.3")
    assert entries == original
    assert len(result) == 2
    assert result[0]["shadowed_by"] == ["Mods/z.dll"]
    assert result[0]["prediction"]["status"] == "excluded"
    assert result[1]["prediction"]["status"] == "candidate"


def test_equal_versions_unknown_independent_of_path_order_or_hash():
    entries = [assembly("Mods/z.dll", "1.0.0.0"), assembly("Mods/a.dll", "1.0.0.0")]
    entries[0]["sha256"] = "a" * 64
    entries[1]["sha256"] = "b" * 64
    assert {e["prediction"]["status"] for e in choose_candidates(entries, "0.7.3")} == {"unknown"}


def test_same_names_in_separate_stages_and_different_case_do_not_shadow():
    entries = [assembly("Mods/a.dll", "1.0.0.0"), assembly("Plugins/b.dll", "2.0.0.0"), assembly("Mods/c.dll", "2.0.0.0", name="same")]
    assert all(e["prediction"]["status"] == "candidate" for e in choose_candidates(entries, "0.7.3"))


@pytest.mark.parametrize("version", [None, "0.46D", "1", "1.2.3.4.5", "2147483648.0"])
def test_unreadable_version_cannot_choose_known_other_candidate(version):
    entries = [assembly("Mods/a.dll", version), assembly("Mods/b.dll", "1.0.0.0")]
    assert all(e["prediction"]["status"] == "unknown" for e in choose_candidates(entries, "0.7.3"))


def test_metadata_eligibility_and_user_libs_difference():
    entries = [assembly("Mods/a.dll", "1.0", melon=False), assembly("UserLibs/a.dll", "1.0", melon=False), assembly("Mods/native.dll", None, managed=False), assembly("Mods/unknown.dll", None, managed=None)]
    statuses = [e["prediction"]["status"] for e in choose_candidates(entries, "0.7.3")]
    assert statuses == ["excluded", "candidate", "excluded", "unknown"]


def test_unparsed_file_may_shadow_known_candidate_but_not_other_stage():
    unknown = assembly("Mods/unparsed.dll", None, managed=None)
    unknown["assembly_name"] = None
    result = choose_candidates([assembly("Mods/good.dll", "1.0"), unknown,
                                assembly("UserLibs/good.dll", "1.0")], "0.7.3")
    assert [item["prediction"]["status"] for item in result] == ["unknown", "unknown", "candidate"]


def test_invalid_and_non_dll_paths():
    assert policy("Mods/a.txt")["status"] == "excluded"
    for path in ["Mods/../a.dll", "C:/Mods/a.dll", "/Mods/a.dll", "Other/a.dll"]:
        assert policy(path)["status"] == "unknown"
