# Dependency and platform evidence

Checked 2026-10-17 for this vault scope only. The host installed
`keyring==25.7.0` from default PyPI; this package did not modify root dependencies
or the model environment. Existing `cryptography==49.0.0` is reused.

- [Exact keyring PyPI metadata](https://pypi.org/pypi/keyring/25.7.0/json):
  stable 25.7.0, MIT, Python >=3.9; universal wheel 39,160 bytes, uploaded
  2025-11-16. [Tagged source](https://github.com/jaraco/keyring/tree/v25.7.0)
  and [official project documentation](https://keyring.readthedocs.io/en/latest/)
  show the native backends and maintained project (development docs identify
  25.7.1). This module imports only fixed allowlisted native classes, bypassing
  keyring backend discovery/config selection.
- Linux installation brought SecretStorage 3.5.0 (BSD-3-Clause), jeepney 0.9.0
  (MIT), jaraco.classes 3.4.0, jaraco.functools 4.6.0, jaraco.context 6.1.2 and
  more-itertools 11.2.1. Native DBus support is dependency work; no additional
  service, paid API or heavyweight framework was introduced. Metadata sources:
  [SecretStorage](https://pypi.org/pypi/SecretStorage/3.5.0/json),
  [jeepney](https://pypi.org/pypi/jeepney/0.9.0/json).
- A focused public [OSV exact-package/version query](https://google.github.io/osv.dev/post-v1-query/)
  for PyPI `keyring` 25.7.0 returned `{}` at check time. That means no matching
  entry returned by that query, not proof of absence of defects or an audit of
  unrelated dependencies. Fixed backend selection addresses the local policy
  requirement regardless of third-party backend availability.
- cryptography is mature upstream crypto, dual Apache-2.0/BSD licensed. The
  installed 49.0.0 implementation was exercised in actual owned-process checks;
  [49.0.0 Scrypt source](https://github.com/pyca/cryptography/blob/49.0.0/src/cryptography/hazmat/primitives/kdf/scrypt.py)
  confirms the used constructor/derive API. Official
  [AES-GCM guidance](https://cryptography.io/en/latest/hazmat/primitives/aead/)
  and [Fernet guidance](https://cryptography.io/en/latest/fernet/) describe the
  primitives and limitations. Latest documentation may advance beyond installed
  49.0.0; local checks and tagged source are the version-specific evidence.
- Windows ACL implementation follows Microsoft's
  [ACL semantics](https://learn.microsoft.com/en-us/windows/win32/secauthz/access-control-lists),
  [CreateDirectoryW security attributes](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createdirectoryw)
  and [GetVolumeInformationW filesystem flags](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumeinformationw).
  A null/missing DACL is not owner-only security; filesystem persistent ACL
  support must be present. Code compilation on Linux is not Windows validation.

Actual native availability here: direct SecretService backend priority 5;
default collection unlocked. An owned UUID entry was really written/read across
broker clients, retained across broker restart and explicitly deleted after
acceptance. No existing user entry was read or modified. macOS Keychain and
Windows Credential Manager availability/ACL behavior remain unknown until real
device verification.
