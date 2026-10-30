"""Independent Windows keyring records; no legacy namespace or plaintext store."""
import hashlib
from backend.app.identity import KEYRING_SERVICE


class NativeCredentials:
    """Use only our compound record, avoiding WinVault's bare-service migration."""
    def __init__(self):
        self._vault = None

    @property
    def vault(self):
        if self._vault is None:
            from keyring.backends.Windows import WinVaultKeyring
            self._vault = WinVaultKeyring()
        return self._vault

    def get_password(self, service, account):
        record = self.vault._read_credential(self.vault._compound_name(account, service))
        return record.value if record and record['UserName'] == account else None

    def set_password(self, service, account, key):
        self.vault._set_password(self.vault._compound_name(account, service), account, key)

    def delete_password(self, service, account):
        self.vault._delete_password(self.vault._compound_name(account, service))


class CredentialStore:
    def __init__(self, paths, backend=None):
        self.paths = paths
        self._backend = backend if backend is not None else NativeCredentials()
        self._cache = {}

    def account(self, config):
        value = str(self.paths.root.resolve()).casefold()+'\0'+config['provider']+'\0'+config['base_url']
        # The migrated default retains the existing record byte-for-byte.
        # Other profiles may deliberately use different keys at one endpoint.
        if config.get('_provider_id', 'default') != 'default':
            value += '\0' + config['_provider_id']
        return 'ai-'+hashlib.sha256(value.encode('utf-8')).hexdigest()

    @property
    def secrets(self):
        return [key for key, _ in self._cache.values() if key]

    def get(self, config):
        account = self.account(config)
        if account not in self._cache:
            try:
                key = self._backend.get_password(KEYRING_SERVICE, account)
                self._cache[account] = (key or None, 'saved' if key else 'missing')
            except Exception:
                self._cache[account] = (None, 'unavailable')
        return self._cache[account]

    def set(self, config, key, remember):
        account = self.account(config)
        self._cache[account] = (key, 'session')
        if not remember:
            return '密钥仅在本次管理器会话使用。'
        try:
            self._backend.set_password(KEYRING_SERVICE, account, key)
            if self._backend.get_password(KEYRING_SERVICE, account) != key:
                raise ValueError('Credential write not confirmed')
        except Exception:
            return '无法保存到 Windows 凭据；此密钥仅在本次管理器会话使用。'
        self._cache[account] = (key, 'saved')
        return '密钥已保存到独立的 Windows 凭据记录。'

    def clear(self, config):
        account = self.account(config)
        # Session-only profiles need not have a Windows record to delete.
        # A failed vault read remains an error, never a successful deletion.
        if self._backend.get_password(KEYRING_SERVICE, account) is not None:
            self._backend.delete_password(KEYRING_SERVICE, account)
        self._cache.pop(account, None)

    def close(self):
        self._cache.clear()
