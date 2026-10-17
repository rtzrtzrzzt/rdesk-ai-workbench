class VaultError(Exception):
    """Stable safe error; never attach passwords, keys or raw backend exceptions."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
