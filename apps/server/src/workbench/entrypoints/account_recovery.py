"""Retired login-password recovery command; vault recovery remains independent."""


def recover() -> None:
    raise SystemExit(
        "recover-account 已退役：本机工作台不再使用登录密码。"
        "请用回环地址打开工作台以建立新会话；凭据保险库仍需独立口令或系统Keyring解锁。"
    )
