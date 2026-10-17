"""Explicit cryptographic/IPC mechanism checks, never provider-success evidence."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

from cryptography.fernet import Fernet

from workbench.infrastructure.vault.client import VaultAdminClient, VaultClient
from workbench.infrastructure.vault.errors import VaultError
from workbench.infrastructure.vault.ipc import MAX_FRAME, receive_frame
from workbench.infrastructure.vault.migration import legacy_reencrypt
from workbench.infrastructure.vault.native import NativeKeyring
from workbench.infrastructure.vault.private import read_json, write_private


def expect(code, action):
    try:
        action()
    except VaultError as error:
        assert error.code == code, (error.code, code)
        return error.code
    raise AssertionError("Expected bounded vault refusal: " + code)


def worker_check(data_dir: Path, ciphertext: str, expected_nonce: str) -> dict:
    program = """
import json,os,sys
from pathlib import Path
from workbench.infrastructure.vault import VaultClient
values=json.load(sys.stdin)
client=VaultClient(Path(values['data_dir']))
print(json.dumps({'pid':os.getpid(),'state':client.status()['state'],
                  'nonce_matches':client.decrypt(values['ciphertext'])==values['expected_nonce']}))
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        input=json.dumps(
            {
                "data_dir": str(data_dir),
                "ciphertext": ciphertext,
                "expected_nonce": expected_nonce,
            }
        ),
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    return json.loads(result.stdout)


def run(root: Path):
    root.mkdir(mode=0o700, exist_ok=False)
    report = {
        "scope": "Cryptographic/IPC mechanism tests with owned random nonce strings; "
        "no provider key/API test",
        "platform": sys.platform,
        "cases": {},
    }
    probe = NativeKeyring()
    report["native_probe"] = probe.probe()
    processes = []
    owned_native_ids = []

    def start(directory: Path, name: str, ttl=60):
        log = (root / (name + "-broker.log")).open("wb")
        command = [
            sys.executable,
            "-m",
            "workbench.infrastructure.vault.broker",
            "--data-dir",
            str(directory),
            "--ttl-seconds",
            str(ttl),
            "--request-timeout-seconds",
            "0.5",
        ]
        # Deliberately configured unsupported selector must not affect the explicit backend.
        env = os.environ.copy()
        env["PYTHON_KEYRING_BACKEND"] = "keyrings.alt.file.PlaintextKeyring"
        process = subprocess.Popen(command, stdout=log, stderr=log, env=env, start_new_session=True)
        processes.append((process, log))
        client = VaultClient(directory)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Owned broker failed to start; inspect its private log")
            try:
                state = client.status()
                return process, client, VaultAdminClient(directory), state
            except VaultError as error:
                if error.code not in {"BROKER_UNAVAILABLE", "INVALID_BROKER"}:
                    raise
            time.sleep(0.03)
        raise TimeoutError("Owned broker readiness deadline")

    def stop(process, admin):
        admin.stop()
        process.wait(timeout=5)
        assert process.returncode == 0

    try:
        if probe.available:
            native_dir = root / "native"
            native_dir.mkdir(mode=0o700)
            process, client, admin, state = start(native_dir, "native")
            assert state["state"] == "uninitialized" and state["keyring_available"]
            status = admin.initialize()
            assert status["backend"] == "keyring" and status["state"] == "unlocked"
            vault_id = read_json(native_dir / "vault/envelope.json")["vault_id"]
            owned_native_ids.append(vault_id)
            nonce = secrets.token_urlsafe(32)
            ciphertext = client.encrypt(nonce)
            worker = worker_check(native_dir, ciphertext, nonce)
            assert worker["nonce_matches"] and worker["pid"] != os.getpid()
            admin.lock()
            expect("VAULT_LOCKED", lambda: client.decrypt(ciphertext))
            admin.unlock()
            assert client.decrypt(ciphertext) == nonce
            before = (native_dir / "vault/envelope.json").read_bytes()
            expect("ALREADY_INITIALIZED", lambda: admin.initialize())
            assert (native_dir / "vault/envelope.json").read_bytes() == before
            stop(process, admin)
            process, client, admin, state = start(native_dir, "native-restart")
            assert state["state"] == "locked"
            expect("VAULT_LOCKED", lambda: client.decrypt(ciphertext))
            admin.unlock()
            assert worker_check(native_dir, ciphertext, nonce)["nonce_matches"]
            stop(process, admin)
            report["cases"]["native_keyring_real_set_get_cross_process_lock_restart"] = True
            report["cases"]["configured_alt_selector_ignored"] = True
        else:
            report["cases"]["native_keyring_real_set_get_cross_process_lock_restart"] = (
                "unavailable"
            )

        fallback_dir = root / "fallback"
        fallback_dir.mkdir(mode=0o700)
        process, client, admin, state = start(fallback_dir, "fallback")
        password = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        assert state["state"] == "uninitialized"
        expect(
            "ADMIN_REQUIRED",
            lambda: client._call("initialize", {"mode": "passphrase", "passphrase": password}),
        )
        status = admin.initialize("passphrase", passphrase=password)
        assert status["backend"] == "passphrase" and status["state"] == "unlocked"
        ciphertext = client.encrypt(nonce)
        assert worker_check(fallback_dir, ciphertext, nonce)["nonce_matches"]
        envelope = (fallback_dir / "vault/envelope.json").read_bytes()
        metadata = json.loads(envelope)
        assert password.encode() not in envelope and nonce.encode() not in envelope
        assert "master" not in metadata and "passphrase" not in metadata
        assert len(metadata["wrapped_master"]) == 64
        expect("ADMIN_REQUIRED", lambda: client._call("lock"))
        admin.lock()
        expect("VAULT_LOCKED", lambda: client.encrypt(nonce))
        expect("UNLOCK_FAILED", lambda: admin.unlock(passphrase=secrets.token_urlsafe(32)))
        assert client.status()["state"] == "locked"
        admin.unlock(passphrase=password)
        assert worker_check(fallback_dir, ciphertext, nonce)["nonce_matches"]
        expect("ALREADY_INITIALIZED", lambda: admin.initialize("passphrase", passphrase=password))
        assert (fallback_dir / "vault/envelope.json").read_bytes() == envelope
        expect("INVALID_CIPHERTEXT", lambda: client.decrypt(ciphertext[:-5] + "xxxxx"))
        report["cases"]["passphrase_cross_process_lock_wrong_password_no_overwrite"] = True
        report["cases"]["data_client_cannot_manage_without_admin_capability"] = True
        report["cases"]["envelope_has_no_plaintext_password_or_master_field"] = True

        legacy_dir = root / "owned-legacy-mechanism"
        legacy_dir.mkdir(mode=0o700)
        legacy_key_path = legacy_dir / "owned-test-legacy.key"
        legacy_key = Fernet.generate_key()
        write_private(legacy_key_path, legacy_key)
        legacy_ciphertext = Fernet(legacy_key).encrypt(nonce.encode()).decode()
        converted = legacy_reencrypt(legacy_ciphertext, legacy_key_path, client)
        assert client.decrypt(converted) == nonce
        assert legacy_key_path.read_bytes() == legacy_key
        assert Fernet(legacy_key).decrypt(legacy_ciphertext.encode()).decode() == nonce
        report["cases"]["explicit_legacy_helper_preserves_owned_old_fixture"] = True

        if os.name != "nt":
            descriptor = read_json(fallback_dir / "vault/broker.json")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.connect(descriptor["socket"])
                connection.sendall(b'{"unfinished":')
                response = receive_frame(connection, deadline=time.monotonic() + 2)
                assert response["error"]["code"] == "BROKER_TIMEOUT"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.connect(descriptor["socket"])
                connection.sendall(b"x" * (MAX_FRAME + 1))
                response = receive_frame(connection, deadline=time.monotonic() + 2)
                assert response["error"]["code"] == "FRAME_TOO_LARGE"
            modes = {
                p.name: oct(p.stat().st_mode & 0o777) for p in (fallback_dir / "vault").iterdir()
            }
            assert modes["broker.sock"] == "0o600" and modes["envelope.json"] == "0o600"
            report["private_modes"] = modes
            report["cases"]["actual_uds_frame_limit_and_partial_request_timeout"] = True
        stop(process, admin)
        process, client, admin, state = start(fallback_dir, "fallback-restart")
        assert state["state"] == "locked"
        expect("VAULT_LOCKED", lambda: client.decrypt(ciphertext))
        admin.unlock(passphrase=password)
        assert worker_check(fallback_dir, ciphertext, nonce)["nonce_matches"]
        stop(process, admin)
        assert not (fallback_dir / "vault/broker.sock").exists()
        assert not (fallback_dir / "vault/admin.token").exists()
        assert not (fallback_dir / "vault/broker.json").exists()
        assert (fallback_dir / "vault/envelope.json").read_bytes() == envelope
        report["cases"]["fallback_restart_requires_explicit_unlock_and_stop_cleans_ipc"] = True

        ttl_dir = root / "ttl"
        ttl_dir.mkdir(mode=0o700)
        process, client, admin, state = start(ttl_dir, "ttl", ttl=1)
        admin.initialize("passphrase", passphrase=password)
        client.encrypt(nonce)
        time.sleep(1.1)
        assert client.status()["state"] == "locked"
        assert client.status()["last_lock_reason"] == "ttl_expired"
        expect("VAULT_LOCKED", lambda: client.encrypt(nonce))
        stop(process, admin)
        report["cases"]["absolute_ttl_locks_without_provider_access"] = True
        report["passed"] = True
    finally:
        for process, log in processes:
            if process.poll() is None:
                process.terminate()  # Exact subprocess created by this helper only.
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            log.close()
        for vault_id in owned_native_ids:
            probe.delete_owned_test_entry(vault_id)
        report["owned_native_test_entries_deleted"] = len(owned_native_ids)
        logs = b"".join(path.read_bytes() for path in root.glob("*-broker.log"))
        if "password" in locals():
            assert password.encode() not in logs and nonce.encode() not in logs
        report["logs_contain_no_test_passphrase_or_nonce"] = True
        (root / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.output_dir)


if __name__ == "__main__":
    main()
