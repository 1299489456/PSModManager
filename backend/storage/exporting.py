"""Deliberately small, portable Profile manifest: never serialize stored payloads."""
import re


def portable_version(value):
    # Raw versions are untrusted author/user input, not SemVer. Unsafe metadata
    # is omitted rather than leaking a local path, URL, credential or command.
    if value is None:
        return None
    if not re.fullmatch(r'(?i)v?[0-9]{1,6}(?:\.[0-9]{1,6}){0,3}'
                        r'(?:[-.](?:alpha|beta|rc|dev|pre|local|build|preview|snapshot)(?:[.-]?[0-9]{1,6})?)?'
                        r'(?:\+[0-9]{1,12})?', value):
        return None
    return value


def profile_manifest(profile, releases, packages):
    """All references were resolved by the repository; no paths or bytes read."""
    selections = []
    for selection in profile.selections:
        release = releases[str(selection.release_id)]
        package = packages[str(release.package_id)]
        selections.append({
            'release_id': str(release.release_id),
            'manifest_hash': release.manifest_hash,
            'version_raw': portable_version(release.version_raw),
            'selected': selection.selected,
            'source': {'kind': package.source.kind,
                       'observed_at': package.source.observed_at.isoformat().replace('+00:00', 'Z')},
        })
    return {'schema_version': 1, 'profile_id': str(profile.profile_id),
            'revision': profile.revision, 'selections': selections}
