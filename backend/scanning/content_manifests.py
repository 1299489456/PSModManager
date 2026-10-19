"""Bounded static declarations; never proof of runtime registration or ownership.

Bundle fields follow docs/evidence_appendix.md. Per-content Id is an explicit
supported schema, not a claim that every framework generator uses that schema.
"""
import json

MAX_RESOURCE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 256 * 1024
MAX_CONTENT_IDS = 256
MAX_DEPTH = 24
FRAMEWORKS = {
    'ItemForge.items.json': ('itemforge', 'Items', 'item'),
    'CharacterForge.characters.json': ('characterforge', 'Characters', 'character'),
}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _text(value, *, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError('INVALID_STRING')
    return value


def _depth(value, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError('JSON_DEPTH_LIMIT')
    if isinstance(value, dict):
        for child in value.values():
            _depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _depth(child, depth + 1)


def analyze(metadata, *, artifact_sha256, observed_at):
    """Return bounded source-attributed declarations without accessing files."""
    result = dict(roles=[], manifests=[], content_ids=[], requirements=[],
                  diagnostics=[], melon_info=None, assembly_name=None,
                  assembly_version=None, registration_status='unknown')
    metadata = metadata if isinstance(metadata, dict) else {}
    roles = set()

    def source(reference):
        return dict(kind='embedded_metadata',
                    reference=f'artifact:{artifact_sha256}#{reference}', observed_at=observed_at)

    def diagnostic(code, reference):
        if len(result['diagnostics']) < 32:
            result['diagnostics'].append(dict(code=code, source=source(reference), confidence='confirmed'))

    result['resource_coverage'] = metadata.get('resource_coverage', {'status': 'complete'})
    if not isinstance(result['resource_coverage'], dict):
        result['resource_coverage'] = {'status': 'partial'}
    if result['resource_coverage'].get('status') != 'complete':
        diagnostic('RESOURCE_COVERAGE_PARTIAL', 'resources')

    for key in ('assembly_name', 'assembly_version'):
        try:
            result[key] = _text(metadata.get(key), optional=True)
        except ValueError:
            diagnostic('IDENTITY_TEXT_LIMIT', key)
    attributes = metadata.get('custom_attributes', [])
    if not isinstance(attributes, list):
        attributes = []
        diagnostic('ATTRIBUTE_SCHEMA_INVALID', 'attributes')
    for attribute in attributes[:256]:
        if not isinstance(attribute, dict) or attribute.get('type') != 'MelonLoader.MelonInfoAttribute':
            continue
        args = attribute.get('arguments')
        if attribute.get('decoded') is not True or not isinstance(args, list) or len(args) not in (4, 5):
            diagnostic('MELON_INFO_UNREADABLE', 'attribute:MelonLoader.MelonInfoAttribute')
            continue
        try:
            values = [_text(value, optional=True) for value in args]
        except ValueError:
            diagnostic('MELON_INFO_UNREADABLE', 'attribute:MelonLoader.MelonInfoAttribute')
            continue
        if result['melon_info'] is not None:
            diagnostic('MELON_INFO_DUPLICATE', 'attribute:MelonLoader.MelonInfoAttribute')
            continue
        result['melon_info'] = dict(entry_type=values[0], name=values[1], version_raw=values[2],
                                   author=values[3], source=source('attribute:MelonLoader.MelonInfoAttribute'),
                                   confidence='confirmed')
        roles.add('melon_mod')

    resources = metadata.get('resources', [])
    if not isinstance(resources, list):
        resources = []
        diagnostic('RESOURCE_SCHEMA_INVALID', 'resources')
    if len(resources) > 64:
        diagnostic('RESOURCE_COUNT_LIMIT', 'resources')
        resources = resources[:64]
    known = [r for r in resources if isinstance(r, dict) and r.get('name') in FRAMEWORKS]
    duplicates = {r['name'] for r in known if sum(x['name'] == r['name'] for x in known) > 1}
    seen = set()
    total_bytes = 0
    for resource in known:
        name = resource['name']
        if name in seen:
            continue
        seen.add(name)
        framework, collection, id_type = FRAMEWORKS[name]
        reference = 'resource:' + name
        manifest = dict(framework=framework, resource=name, bundle_id=None,
                        bundle_version_raw=None, generator_version_raw=None,
                        required_runtime_api=None, content_ids=[], source=source(reference),
                        confidence='confirmed', status='invalid')
        result['manifests'].append(manifest)
        try:
            if name in duplicates:
                raise ValueError('DUPLICATE_RESOURCE')
            if resource.get('embedded') is not True:
                raise ValueError('RESOURCE_NOT_EMBEDDED')
            if resource.get('status') == 'skipped_limit':
                raise ValueError('RESOURCE_BYTES_LIMIT')
            text = resource.get('json_text')
            if not isinstance(text, str):
                raise ValueError('JSON_UNAVAILABLE')
            size = len(text.encode('utf-8'))
            total_bytes += size
            if size > MAX_RESOURCE_BYTES or total_bytes > MAX_TOTAL_BYTES:
                raise ValueError('RESOURCE_BYTES_LIMIT')
            declared_size = resource.get('size', size)
            if type(declared_size) is not int or not 0 <= declared_size <= MAX_RESOURCE_BYTES:
                raise ValueError('RESOURCE_BYTES_LIMIT')
            data = json.loads(text, object_pairs_hook=_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))
            _depth(data)
            if not isinstance(data, dict):
                raise ValueError('MANIFEST_SCHEMA_INVALID')
            bundle = _text(data.get('BundleId'))
            api = data.get('RequiredRuntimeApi')
            if type(api) is not int or not 0 <= api <= 2147483647:
                raise ValueError('RUNTIME_API_INVALID')
            version = _text(data.get('BundleVersion'), optional=True)
            generator = _text(data.get('GeneratorVersion'), optional=True)
            if len(result['content_ids']) >= MAX_CONTENT_IDS:
                raise ValueError('CONTENT_ID_LIMIT')
            ids = [dict(framework=framework, type='bundle', value=bundle,
                        source=source(reference + '/BundleId'), confidence='confirmed')]
            status = 'valid'
            try:
                items = data.get(collection)
                if not isinstance(items, list):
                    raise ValueError('CONTENT_ID_SCHEMA_UNKNOWN')
                if len(items) + 1 + len(result['content_ids']) > MAX_CONTENT_IDS:
                    raise ValueError('CONTENT_ID_LIMIT')
                unique = set()
                for index, item in enumerate(items):
                    if not isinstance(item, dict) or 'Id' not in item:
                        raise ValueError('CONTENT_ID_SCHEMA_UNKNOWN')
                    value = _text(item['Id'])
                    if value in unique:
                        raise ValueError('DUPLICATE_CONTENT_ID')
                    unique.add(value)
                    ids.append(dict(framework=framework, type=id_type, value=value,
                                    source=source(reference + '/' + collection + '/' + str(index) + '/Id'),
                                    confidence='confirmed'))
                    if set(item) - {'Id'}:
                        diagnostic('UNKNOWN_CONTENT_FIELDS', reference + '/' + collection)
            except ValueError as exc:
                # Header evidence remains usable even when item schema is unknown.
                ids = ids[:1]
                status = 'partial'
                diagnostic(str(exc), reference + '/' + collection)
            if set(data) - {'BundleId', 'RequiredRuntimeApi', 'BundleVersion', 'GeneratorVersion',
                            'BundleName', 'Author', collection}:
                diagnostic('UNKNOWN_MANIFEST_FIELDS', reference)
            manifest.update(bundle_id=bundle, bundle_version_raw=version, generator_version_raw=generator,
                            required_runtime_api=api, content_ids=ids, status=status)
            roles.add(framework + '_content')
            result['content_ids'].extend(ids)
            result['requirements'].append(dict(framework=framework, capability=framework + '.runtime_api',
                minimum=api, requirement_raw=api, source=source(reference + '/RequiredRuntimeApi'), confidence='confirmed'))
        except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
            code = str(exc) if type(exc) is ValueError and str(exc).isupper() else 'JSON_INVALID'
            diagnostic(code, reference)
    result['roles'] = sorted(roles) or ['unknown']
    return result
