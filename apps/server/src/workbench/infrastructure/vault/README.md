# Provider vault broker

This package owns provider-master storage and local IPC. Importing its public
API performs no disk, socket or Keyring I/O. Provider records, HTTP, launcher
and UI integrate the public API below; they do not own the broker's master
storage or IPC framing. Final product acceptance belongs to the host.

## Public API and lifecycle

```python
from pathlib import Path
from workbench.infrastructure.vault import VaultClient, VaultAdminClient, VaultError

data_dir = Path("/absolute/private/data")
worker = VaultClient(data_dir)  # synchronous; use a worker thread in async routes
state = worker.status()

# Only the authenticated owner-management route may expose these operations.
owner = VaultAdminClient(data_dir)
owner.initialize()  # prefer the allowlisted native Keyring
# Explicit alternative with an independent vault passphrase:
# owner.initialize("passphrase", passphrase=provided_secret)
owner.lock()
owner.unlock()  # passphrase mode requires passphrase=provided_secret

# Provider connector code only; never return plaintext/admin capabilities to a model.
ciphertext = worker.encrypt(provider_secret)
provider_secret = worker.decrypt(ciphertext)
```

`initialize`, `unlock` and `lock` are distinct operations. Successful explicit
initialization leaves the vault unlocked. Broker startup, status and client
construction never initialize or unlock it. Existing envelopes cannot be
reinitialized. Errors have sanitized `VaultError.code` and `.message`; examples
are `VAULT_LOCKED`, `VAULT_UNINITIALIZED`, `KEYRING_UNAVAILABLE`,
`UNLOCK_FAILED`, `ALREADY_INITIALIZED`, `BROKER_UNAVAILABLE`, `BROKER_TIMEOUT`
and `INSECURE_PRIVATE_PERMISSIONS`. Locked/unavailable means provider credentials
are unavailable; there is no plaintext fallback or legacy-key decrypt fallback.

`status()` includes `state` (`uninitialized`, `locked`, `unlocked`), `backend`,
`initialized`, `initialization_complete`, `keyring_available`, `keyring_backend`,
`keyring_reason`, absolute TTL/remaining seconds and `last_lock_reason`.
`keyring_available` describes the last actual startup/explicit-operation probe;
it does not promise a future OS permission decision or auto-unlock anything.

## Storage and cryptography

Native backends are direct, fixed classes: `keyring.backends.SecretService.Keyring`,
`keyring.backends.macOS.Keyring`, `keyring.backends.Windows.WinVaultKeyring`.
The implementation does not invoke automatic backend selection, third-party
entrypoints, configured backend paths or `keyrings.alt`. OS get/set happen only
inside the broker. A new UUID account uses service `AIWorkbench.ProviderVault.v1`.
Native initialization persists a pending intent before OS write, confirms an
actual read-back, then marks ready. Failed/ambiguous writes retain that intent;
explicit unlock can reconcile an existing entry. Missing entries never cause
replacement-master generation or automatic passphrase fallback.

Passphrase mode is explicit. The caller supplies an independent 12–1024 UTF-8
byte passphrase; the package never takes a login password implicitly and cannot
prove that a user chose a different password. A random 32-byte master is wrapped
with AES-GCM using Scrypt `N=131072,r=8,p=1,length=32`, random 16-byte salt and
12-byte nonce. Version/vault-ID AAD binds the envelope. Fixed KDF parameters are
validated before derivation (approximately 128 MiB Scrypt memory). The private
envelope contains no plaintext master or passphrase. Provider values use mature
Fernet with a `vault:v1:` version prefix and a 64 KiB plaintext bound.

The broker alone retains the master in memory, with an absolute unlock TTL
(default 900 seconds, allowed 1–86400); traffic does not extend it. Lock drops
crypto references and overwrites the retained mutable master buffer. Python and
native-library copies are not claimed securely zeroized. A lock cannot retract
provider plaintext already handed to a caller or stop an already-sent request:
connectors must not cache decrypted keys across lock/TTL or future operations.

## Broker and process ownership

From the project environment (with `workbench` importable):

```sh
.venv/bin/python -m workbench.infrastructure.vault.broker \
  --data-dir /absolute/private/data --ttl-seconds 900
```

CLI arguments contain paths/policy only, never password/master/token. The final
data directory may be created if its parent exists; existing data and vault
directories must already be private. POSIX directories require owner UID and
0700-equivalent permissions; files and UDS require 0600-equivalent permissions.
Unsafe existing directories are rejected, not silently chmodded. The broker
uses `data_dir/vault/` and owns its native file lock. For Linux UDS paths of
100 encoded bytes or longer, broker and client use a private directory FD via
`/proc/self/fd/` during bind/connect. The FD's directory type, UID and permissions
are checked; the actual socket, descriptor path and cleanup remain in the
original private vault directory. Other Unix platforms still require the full
UDS path to be shorter than 100 encoded bytes. Readiness prints only
`{"broker_ready":true}` after its private descriptor is published; poll client
status for actual state. The host launcher verifies that status, all four
component identities, API listener ownership and Worker heartbeats before
reporting successful startup.

Linux/macOS use private UDS with peer UID checks; Linux clients also verify
server PID. Windows uses loopback `127.0.0.1` on an ephemeral port plus a random
temporary transport token. A separate random admin capability protects
initialize/unlock/lock/stop on every platform. Data clients do not read it.
This is an API separation within a **same OS account trust boundary**: malicious
code under that account can read its private files/memory. It is not a sandbox
against that code, administrators or debuggers.

Windows creates directories using native owner-SID protected DACLs and verifies
owner/access entries and persistent filesystem ACL support. A missing/null/
foreign-access DACL or unsupported filesystem fails closed. POSIX chmod is not
treated as Windows ACL enforcement. Windows and macOS code paths have not been
validated on their real operating systems; they require device acceptance.

IPC accepts one size-bounded JSON frame (128 KiB) per connection with at most
eight concurrent handlers. Client absolute request deadline defaults to 10s
(0.1–60s); the server bounds frame reads and response writes. OS Keyring calls
and an in-progress KDF cannot be safely cancelled at an arbitrary instruction.
A timed-out initialization may therefore finish: inspect status, do not blindly
replay it. Persisted intent and exclusive envelope creation prevent overwriting
the master. No password-bearing tracebacks or request logs are emitted.

`VaultAdminClient.stop()` and owned SIGTERM/SIGINT normally remove only this
instance's IPC descriptor/socket/capability files; the envelope and lock file
remain. After an abnormal crash, startup refuses stale IPC by default. Explicit
`--cleanup-stale` holds the native lock and requires the recorded process to be
gone (or Linux process-start identity changed); ambiguous/live identity fails
closed. No unrelated process or legacy data is stopped/deleted.

## Legacy integration

`migration.legacy_reencrypt(ciphertext, legacy_key_path, unlocked_client)`
decrypts one explicitly supplied old value and returns a new versioned value.
It never writes database rows, deletes the old key/ciphertext or uses the old
master for new encryption. The host must authorize migration, verify/commit the
replacement transactionally and retain rollback material according to its
policy. Session/CSRF HMAC material remains independent from this provider master.
Exclude passphrases, plaintext provider values, tokens and legacy keys from
HTTP logs, model context and diagnostic bundles; UI authorization belongs to
the host, not to the local broker's knowledge of login sessions.

## Recorded mechanism evidence (2026-10-17)

These are cryptographic/IPC checks with owned random nonce strings, not provider
keys or provider/API success evidence. No model inference was used.

- `/tmp/vlt-check-20261017-2/result.json`: real Linux SecretService set/get,
  cross-process clients, native lock/restart/unlock, configured alternate-backend
  selector ignored, passphrase cross-process round trip, wrong password,
  no overwrite, tampered ciphertext rejection, admin capability, private modes,
  actual oversized/partial frame handling, TTL, normal cleanup, explicit legacy
  fixture conversion and retained legacy fixture. All recorded cases passed;
  exactly one owned native test entry was deleted. Logs contain no test
  passphrase or nonce.
- `/tmp/vlt-timeout-20261017/result.json`: a 0.1s client deadline elapsed in
  0.100455s, returning `BROKER_TIMEOUT`; authorized initialization completed,
  status reconciled it, repeated initialization refused without changing the
  envelope, and the owned broker stopped normally.
- `/tmp/vlt-perms-20261017/result.json`: 0755 base directory refused before a
  vault child was created; a later changed private directory was refused by the
  client. Owned broker stopped normally after restoring its permissions.
- `/tmp/vlt-paths-20261017/result.json`: client construction performs no
  symlink-resolution I/O; linked data directories are refused before creating
  a vault child or probing the native backend.
- `/tmp/vlt-check-20261017-1/result.json` preserves the initial readiness race:
  missing descriptor incorrectly reported `INVALID_VAULT_FILE`. No initialization
  occurred. It was corrected to `BROKER_UNAVAILABLE` and the full second check
  passed. This was a startup error-classification issue, not crypto failure.

`acceptance.py --output-dir <fresh-private-check-directory>` is an explicit
owned-process mechanism runner, not a product startup hook. It creates only its
test entries/directories and never stops another service. The random fallback
test passwords are deliberately not saved. Source/dependency limits are in
[sources.md](sources.md). Final host facade/launcher/UI integration and real
Windows/macOS acceptance remain outside these auxiliary checks.

## Host integration and later product evidence

The host facade, launcher-managed broker and settings UI are now connected.
Secrets remain in the protected broker/host path; browser recovery metadata does
not persist passphrases. An uncertain settings request is reconciled by its
original request ID. The host `POST /api/v1/requests/{id}/close` shares the request
admission lock: an in-flight request cannot be sealed, an existing receipt must be
read, and sealing prevents a late request with the same ID from being admitted.
It does not cancel broker work or reverse an earlier effect.

On 2026-10-18 the main agent used the real Chrome UI to submit while offline,
reload, seal the pending request, then verify that the late identical ID returned
410 and the vault remained uninitialized. Local evidence is
`.local/installation-acceptance/pending-and-preservation-ui-evidence.json`.
This covers that previously unadmitted request, not cancellation of an already
running KDF or every initialization/recovery window. Native Windows/macOS and
their keyring/ACL behavior still require real-device validation.
