"""Named provider profiles and purpose bindings, without plaintext credentials."""
import re
from uuid import uuid4

from .gateway import AIError, normalize_url

DEFAULT = dict(provider='openai_compatible', base_url='https://api.openai.com/v1', model='')
FIELDS = frozenset(DEFAULT)
ID = re.compile(r'(?:default|[a-f0-9]{32})\Z')


def configuration(value, *, allow_empty=False):
    if (not isinstance(value, dict) or set(value) != FIELDS or value['provider'] != 'openai_compatible'
            or not isinstance(value['model'], str) or len(value['model']) > 200
            or not allow_empty and not value['model'].strip()):
        raise AIError('AI_SETTINGS_INVALID')
    return dict(value, base_url=normalize_url(value['base_url']), model=value['model'].strip())


class ProviderRegistry:
    """The assistant lock serializes edits; preferences owns atomic persistence."""
    def __init__(self, preferences):
        self.preferences = preferences

    def write(self, document):
        try:
            self.preferences.update('ai_providers', document)
        except (OSError, ValueError):
            raise AIError('AI_SETTINGS_IO') from None

    def read(self):
        try:
            root = self.preferences.read_config()
        except (OSError, ValueError):
            raise AIError('AI_SETTINGS_IO') from None
        document = root.get('ai_providers')
        if document is None:
            legacy = configuration(root.get('ai', DEFAULT), allow_empty=True)
            document = dict(version=1, providers=[dict(id='default', name='默认服务', **legacy)],
                            bindings=dict(analysis='default', translation='default'))
            self.write(document)
        if (not isinstance(document, dict) or set(document) != {'version', 'providers', 'bindings'}
                or type(document['version']) is not int or document['version'] != 1
                or not isinstance(document['providers'], list) or not 1 <= len(document['providers']) <= 20
                or not isinstance(document['bindings'], dict) or set(document['bindings']) != {'analysis', 'translation'}):
            raise AIError('AI_SETTINGS_INVALID')
        ids = set()
        for row in document['providers']:
            if (not isinstance(row, dict) or set(row) != FIELDS | {'id', 'name'}
                    or not isinstance(row['id'], str) or not ID.fullmatch(row['id']) or row['id'] in ids
                    or not isinstance(row['name'], str) or not row['name'].strip() or len(row['name']) > 80):
                raise AIError('AI_SETTINGS_INVALID')
            configuration({key: row[key] for key in FIELDS}, allow_empty=True)
            ids.add(row['id'])
        if any(not isinstance(value, str) or value not in ids for value in document['bindings'].values()):
            raise AIError('AI_SETTINGS_INVALID')
        return document

    def config(self, purpose='analysis', provider_id=None):
        document = self.read()
        identity = provider_id or document['bindings'][purpose]
        row = self.find(document, identity)
        return dict(configuration({key: row[key] for key in FIELDS}, allow_empty=True), _provider_id=row['id'])

    @staticmethod
    def find(document, identity):
        row = next((row for row in document['providers'] if row['id'] == identity), None)
        if row is None:
            raise AIError('AI_PROVIDER_NOT_FOUND')
        return row

    def save(self, provider_id, name, config):
        config = configuration(config)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise AIError('AI_SETTINGS_INVALID')
        document = self.read()
        if provider_id is None:
            if len(document['providers']) >= 20:
                raise AIError('AI_PROVIDER_LIMIT')
            provider_id = uuid4().hex
            document['providers'].append(dict(id=provider_id, name=name.strip(), **config))
        else:
            row = self.find(document, provider_id)
            row.update(name=name.strip(), **config)
        self.write(document)
        return provider_id

    def bind(self, purpose, provider_id):
        if purpose not in {'analysis', 'translation'}:
            raise AIError('AI_SETTINGS_INVALID')
        document = self.read()
        self.find(document, provider_id)
        document['bindings'][purpose] = provider_id
        self.write(document)

    def delete(self, provider_id):
        document = self.read()
        self.find(document, provider_id)
        if provider_id in document['bindings'].values():
            raise AIError('AI_PROVIDER_IN_USE')
        document['providers'] = [row for row in document['providers'] if row['id'] != provider_id]
        self.write(document)
