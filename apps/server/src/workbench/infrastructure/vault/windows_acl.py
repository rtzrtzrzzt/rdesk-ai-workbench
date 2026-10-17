"""Windows owner-only DACL primitives. No chmod-equivalence claim or ACL fallback."""

from workbench.infrastructure.vault.errors import VaultError


def _api():
    import ctypes
    from ctypes import wintypes as w

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    signatures = [
        (kernel.GetCurrentProcess, [], w.HANDLE),
        (kernel.CloseHandle, [w.HANDLE], w.BOOL),
        (kernel.LocalFree, [pointer], pointer),
        (adv.OpenProcessToken, [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], w.BOOL),
        (
            adv.GetTokenInformation,
            [w.HANDLE, w.DWORD, pointer, w.DWORD, ctypes.POINTER(w.DWORD)],
            w.BOOL,
        ),
        (adv.ConvertSidToStringSidW, [pointer, ctypes.POINTER(w.LPWSTR)], w.BOOL),
        (
            adv.ConvertStringSecurityDescriptorToSecurityDescriptorW,
            [w.LPCWSTR, w.DWORD, ctypes.POINTER(pointer), ctypes.POINTER(w.DWORD)],
            w.BOOL,
        ),
        (
            adv.GetNamedSecurityInfoW,
            [
                w.LPWSTR,
                w.DWORD,
                w.DWORD,
                ctypes.POINTER(pointer),
                ctypes.POINTER(pointer),
                ctypes.POINTER(pointer),
                ctypes.POINTER(pointer),
                ctypes.POINTER(pointer),
            ],
            w.DWORD,
        ),
        (
            adv.GetSecurityDescriptorOwner,
            [pointer, ctypes.POINTER(pointer), ctypes.POINTER(w.BOOL)],
            w.BOOL,
        ),
        (
            adv.GetSecurityDescriptorDacl,
            [pointer, ctypes.POINTER(w.BOOL), ctypes.POINTER(pointer), ctypes.POINTER(w.BOOL)],
            w.BOOL,
        ),
        (adv.EqualSid, [pointer, pointer], w.BOOL),
        (adv.GetAclInformation, [pointer, pointer, w.DWORD, w.DWORD], w.BOOL),
        (adv.GetAce, [pointer, w.DWORD, ctypes.POINTER(pointer)], w.BOOL),
        (kernel.CreateDirectoryW, [w.LPCWSTR, pointer], w.BOOL),
        (kernel.GetVolumePathNameW, [w.LPCWSTR, w.LPWSTR, w.DWORD], w.BOOL),
        (
            kernel.GetVolumeInformationW,
            [
                w.LPCWSTR,
                w.LPWSTR,
                w.DWORD,
                ctypes.POINTER(w.DWORD),
                ctypes.POINTER(w.DWORD),
                ctypes.POINTER(w.DWORD),
                w.LPWSTR,
                w.DWORD,
            ],
            w.BOOL,
        ),
    ]
    for function, arguments, result in signatures:
        function.argtypes, function.restype = arguments, result
    return ctypes, w, adv, kernel


def _owner(ctypes, w, adv, kernel):
    handle, size = w.HANDLE(), w.DWORD()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(handle)):
        raise OSError("OpenProcessToken")
    try:
        adv.GetTokenInformation(handle, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(handle, 1, buffer, size, ctypes.byref(size)):
            raise OSError("GetTokenInformation")
        sid = ctypes.c_void_p.from_buffer(buffer).value
        text = w.LPWSTR()
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise OSError("ConvertSidToStringSid")
        try:
            return buffer, sid, text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(handle)


def create_private_directory(path):
    try:
        ctypes, w, adv, kernel = _api()
        token_buffer, sid, sid_text = _owner(ctypes, w, adv, kernel)
        descriptor = ctypes.c_void_p()
        sddl = f"O:{sid_text}D:P(A;OICI;FA;;;{sid_text})"
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ctypes.byref(descriptor), None
        ):
            raise OSError("ConvertSecurityDescriptor")

        class Attributes(ctypes.Structure):
            _fields_ = [("length", w.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", w.BOOL)]

        attributes = Attributes(ctypes.sizeof(Attributes), descriptor, False)
        try:
            if not kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                raise OSError("CreateDirectory")
        finally:
            kernel.LocalFree(descriptor)
    except Exception:
        raise VaultError(
            "PRIVATE_ACL_UNAVAILABLE", "无法建立Windows当前账户专用ACL；vault拒绝启动。"
        ) from None


def check_private(path):
    try:
        ctypes, w, adv, kernel = _api()
        volume = ctypes.create_unicode_buffer(32768)
        flags = w.DWORD()
        if not kernel.GetVolumePathNameW(str(path), volume, len(volume)):
            raise OSError("GetVolumePathName")
        if not kernel.GetVolumeInformationW(
            volume, None, 0, None, None, ctypes.byref(flags), None, 0
        ):
            raise OSError("GetVolumeInformation")
        if not flags.value & 0x00000008:  # FS_PERSISTENT_ACLS
            raise OSError("File system lacks persistent ACL support")
        token_buffer, sid, _ = _owner(ctypes, w, adv, kernel)
        descriptor = ctypes.c_void_p()
        if (
            adv.GetNamedSecurityInfoW(
                str(path),
                1,
                0x00000001 | 0x00000004,
                None,
                None,
                None,
                None,
                ctypes.byref(descriptor),
            )
            != 0
        ):
            raise OSError("GetNamedSecurityInfo")
        try:
            owner, dacl = ctypes.c_void_p(), ctypes.c_void_p()
            defaulted, present = w.BOOL(), w.BOOL()
            if not adv.GetSecurityDescriptorOwner(
                descriptor, ctypes.byref(owner), ctypes.byref(defaulted)
            ):
                raise OSError("Owner")
            if not adv.EqualSid(owner, sid):
                raise OSError("Wrong owner")
            if not adv.GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
            ):
                raise OSError("DACL")
            if not present.value or not dacl.value:
                raise OSError("Unrestricted DACL")

            class Size(ctypes.Structure):
                _fields_ = [("count", w.DWORD), ("used", w.DWORD), ("free", w.DWORD)]

            info = Size()
            if not adv.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
                raise OSError("AclInformation")
            if not info.count:
                raise OSError("Empty DACL")
            for index in range(info.count):
                ace = ctypes.c_void_p()
                if not adv.GetAce(dacl, index, ctypes.byref(ace)):
                    raise OSError("GetAce")
                # ACCESS_ALLOWED_ACE: header4 + mask4 + SID. Reject other trustees/types.
                if ctypes.c_ubyte.from_address(ace.value).value != 0:
                    raise OSError("Unsupported ACE")
                if not adv.EqualSid(ace.value + 8, sid):
                    raise OSError("Another account has access")
        finally:
            kernel.LocalFree(descriptor)
    except Exception:
        raise VaultError(
            "INSECURE_PRIVATE_ACL", "Windows vault目录或文件不是当前账户专用ACL。"
        ) from None
