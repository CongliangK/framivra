"""Optional local Authenticode check for the private Windows SVG 900 disk scope.

The catalog is passed to WinTrust by its validated, pinned pathname, not by
handle. The provider may use the supplied digest without rereading the member.
No revocation check, mapped-byte or loader closure evidence, or render admission.
"""
from contextlib import ExitStack, contextmanager
import ctypes as C
from ctypes import wintypes as W
import hashlib
from pathlib import Path
import re
import uuid

from ..runtime.windows_browser import _check
from .gfx_svg_win_snapshot import _ordinary, _windows_api
from .gfx_svg_win_system import _SHA256, _facts, _open_pin, _scope_time


_DRIVER = 'f750e6c3-38ee-11d1-85e5-00c04fc295ee'  # DRIVER_ACTION_VERIFY
_GENERIC = '00aac56b-cd44-11d0-8cc2-00c04fc295ee'  # WINTRUST_ACTION_GENERIC_VERIFY_V2
_CATALOG_LEAF = re.compile(r'[a-z0-9][a-z0-9_~.-]{0,219}\.cat', re.I | re.ASCII)
_MAX_CAT_BYTES, _MAX_CATS_BYTES = 32 * 1024**2, 128 * 1024**2  # aggregate counts repeats
_MAX_DER = 16 * 1024
_P, _H, _D, _S, _B = C.c_void_p, W.HANDLE, W.DWORD, W.LPCWSTR, W.BOOL
_INVALID = _H(-1).value


def _validate_catalogs(scope, catalogs):
    # scope already passed _validate_scope. Names align exactly, not by set,
    # so duplicates, omissions, extra rows and swapped associations all fail.
    if scope is None or type(catalogs) is not tuple or len(catalogs) != scope[0]:
        raise ValueError('Windows SVG catalog rows require complete system scope coverage')
    for row, disk in zip(catalogs, scope[1]):
        if (type(row) is not tuple or len(row) != 4 or type(row[0]) is not str or row[0] != disk[0]
                or type(row[1]) is not str or not _CATALOG_LEAF.fullmatch(row[1]) or not _ordinary(row[1])
                or type(row[2]) is not str or not _SHA256.fullmatch(row[2])
                or (row[3] is not None and (type(row[3]) is not str or not _SHA256.fullmatch(row[3])))):
            raise ValueError('Windows SVG invalid or mismatched system catalog row')


class _CatalogInfo(C.Structure):  # CATALOG_INFO
    _fields_ = [('size', _D), ('path', C.c_wchar * 260)]


class _TrustCatalog(C.Structure):  # WINTRUST_CATALOG_INFO
    _fields_ = [('size', _D), ('version', _D), ('catalog', _S), ('tag', _S), ('member', _S),
                ('member_handle', _H), ('digest', _P), ('digest_size', _D), ('ctl', _P), ('admin', _H)]


class _TrustData(C.Structure):  # WINTRUST_DATA (catalog arm of the union)
    _fields_ = [('size', _D), ('policy', _P), ('sip', _P), ('ui', _D), ('revocation', _D), ('choice', _D),
                ('catalog', C.POINTER(_TrustCatalog)), ('action', _D), ('state', _H), ('url', _P),
                ('flags', _D), ('ui_context', _D), ('signature_settings', _P)]


class _ProviderSigner(C.Structure):  # CRYPT_PROVIDER_SGNR
    _fields_ = [('size', _D), ('verify_as_of', W.FILETIME), ('chain_length', _D), ('chain', _P),
                ('signer_type', _D), ('message_signer', _P), ('error', _D), ('countersigners', _D),
                ('countersigner_array', _P), ('chain_context', _P)]


class _ProviderCert(C.Structure):  # CRYPT_PROVIDER_CERT
    _fields_ = [('size', _D), ('cert', _P), ('commercial', _B), ('trusted_root', _B), ('self_signed', _B),
                ('test_cert', _B), ('revoked_reason', _D), ('confidence', _D), ('error', _D),
                ('trust_list', _P), ('trust_list_signer', _B), ('ctl', _P), ('ctl_error', _D),
                ('cyclic', _B), ('chain_element', _P)]


class _CertContext(C.Structure):  # CERT_CONTEXT; pointers are borrowed from provider state
    _fields_ = [('encoding', _D), ('der', _P), ('der_size', _D), ('cert_info', _P), ('store', _H)]


def _guid(text):
    return (C.c_uint32 * 4).from_buffer_copy(uuid.UUID(text).bytes_le)


def _trust_api():
    if (C.sizeof(_H), C.sizeof(_D), C.sizeof(W.LONG), *map(C.sizeof, (
            _TrustCatalog, _TrustData, _ProviderSigner, _ProviderCert, _CertContext))) != (8, 4, 4, 72, 88, 64, 88, 40):
        raise ValueError('Windows SVG system catalog ABI unsupported')
    try:
        dll = C.WinDLL('wintrust.dll', winmode=0x800, use_last_error=True)  # LOAD_LIBRARY_SEARCH_SYSTEM32
        for name, signature in {
            'CryptCATAdminAcquireContext2': (_B, C.POINTER(_H), _P, _S, _P, _D),
            'CryptCATAdminCalcHashFromFileHandle2': (_B, _H, _H, C.POINTER(_D), _P, _D),
            'CryptCATAdminReleaseContext': (_B, _H, _D),
            'WinVerifyTrust': (W.LONG, _H, _P, C.POINTER(_TrustData)),
            'WTHelperProvDataFromStateData': (_P, _H),
            'WTHelperGetProvSignerFromChain': (C.POINTER(_ProviderSigner), _P, _D, _B, _D),
            'WTHelperGetProvCertFromChain': (C.POINTER(_ProviderCert), C.POINTER(_ProviderSigner), _D),
        }.items():
            fn = getattr(dll, name)
            fn.restype, fn.argtypes = signature[0], signature[1:]
    except (AttributeError, OSError):
        raise ValueError('Windows SVG system catalog API unavailable') from None
    return dll


def _signer_sha256(trust, state):
    # Inspect only the verified provider signer's leaf, not CNs or a second chain policy.
    provider = trust.WTHelperProvDataFromStateData(state)
    signer = trust.WTHelperGetProvSignerFromChain(provider, 0, False, 0) if provider else None
    if (not signer or signer.contents.size < C.sizeof(_ProviderSigner) or signer.contents.error
            or not signer.contents.message_signer or not 1 <= signer.contents.chain_length <= 8):
        raise ValueError('Windows SVG system catalog signer state unavailable')
    leaf = trust.WTHelperGetProvCertFromChain(signer, 0)
    if not leaf or leaf.contents.size < C.sizeof(_ProviderCert) or leaf.contents.error or not leaf.contents.cert:
        raise ValueError('Windows SVG system catalog signer certificate unavailable')
    cert = C.cast(leaf.contents.cert, C.POINTER(_CertContext)).contents
    if not (cert.encoding & 1 and cert.der and 0 < cert.der_size <= _MAX_DER):
        raise ValueError('Windows SVG system catalog signer DER invalid')
    return hashlib.sha256(C.string_at(cert.der, cert.der_size)).hexdigest()


def _catalog_digest(trust, admin, rewind, handle, deadline):
    _scope_time(deadline)
    _check(rewind(handle, 0, None, 0), 'SetFilePointerEx (system catalog member)')
    digest, size = (C.c_ubyte * 32)(), _D(32)
    _check(trust.CryptCATAdminCalcHashFromFileHandle2(admin, handle, C.byref(size), digest, 0),
           'CryptCATAdminCalcHashFromFileHandle2 (system catalog member)')
    if size.value != len(digest):
        raise ValueError('Windows SVG system catalog digest size differs')
    return digest


def _verify_member(trust, admin, rewind, path, handle, catalog, expected, pin, deadline, *, signer_pins=None):
    digest = _catalog_digest(trust, admin, rewind, handle, deadline)
    tag = bytes(digest).hex()
    if tag != expected:
        raise ValueError('Windows SVG system catalog member digest differs')
    _check(rewind(handle, 0, None, 0), 'SetFilePointerEx (system catalog member)')
    info = _TrustCatalog(size=C.sizeof(_TrustCatalog), catalog=catalog, tag=tag.upper(), member=path,
                         member_handle=handle, digest=C.cast(digest, _P), digest_size=len(digest), admin=admin)
    # WTD_UI_NONE, WTD_REVOKE_NONE, WTD_CHOICE_CATALOG, WTD_STATEACTION_VERIFY;
    # WTD_CACHE_ONLY_URL_RETRIEVAL | WTD_REVOCATION_CHECK_NONE.
    data = _TrustData(size=C.sizeof(_TrustData), ui=2, revocation=0, choice=2, catalog=C.pointer(info),
                      action=1, flags=0x1010)
    action = _guid(_GENERIC)
    _scope_time(deadline)
    try:
        status = trust.WinVerifyTrust(_INVALID, action, C.byref(data))
        if status != 0 or not data.state:  # LONG equality, not HRESULT success
            raise ValueError(f'Windows SVG system catalog trust failed (0x{status & 0xffffffff:08x})')
        _scope_time(deadline)
        signer = _signer_sha256(trust, data.state) if pin is not None or signer_pins is not None else None
        if (pin is not None and signer != pin) or (signer_pins is not None and signer not in signer_pins):
            raise ValueError('Windows SVG system catalog signer pin differs')
    finally:
        data.action = 2  # WTD_STATEACTION_CLOSE, including on failure and overdue VERIFY
        if trust.WinVerifyTrust(_INVALID, action, C.byref(data)) != 0:
            raise ValueError('Windows SVG system catalog trust state close failed')
    _scope_time(deadline)
    return signer


@contextmanager
def _installed_catalogs(pins, held, system32, signer_pins, deadline):
    """Use the host catalog database as the authority, not a loader sample."""
    if not signer_pins or not all(_SHA256.fullmatch(pin) for pin in signer_pins):
        raise ValueError('Windows SVG requires independently pinned catalog signers')
    parent = system32 / 'CatRoot'
    folder = parent / ('{' + _DRIVER.upper() + '}')
    for path in (parent, folder):
        held._parent(pins, path)
    trust = _trust_api()
    rewind = _windows_api('SetFilePointerEx', _B, _H, C.c_longlong, _P, _D)
    first, info, release = (trust.CryptCATAdminEnumCatalogFromHash, trust.CryptCATCatalogInfoFromContext,
                            trust.CryptCATAdminReleaseCatalogContext)
    first.restype, first.argtypes = _H, (_H, _P, _D, _D, C.POINTER(_H))
    info.restype, info.argtypes = _B, (_H, C.POINTER(_CatalogInfo), _D)
    release.restype, release.argtypes = _B, (_H, _H, _D)
    admin = _H()
    _scope_time(deadline)
    _check(trust.CryptCATAdminAcquireContext2(C.byref(admin), _guid(_DRIVER), 'SHA256', None, 0),
           'CryptCATAdminAcquireContext2 (installed system catalog)')
    if not admin.value or admin.value == _INVALID:
        raise ValueError('Windows SVG system catalog admin handle invalid')

    def verify(member, handle, event_deadline):
        member_deadline = min(deadline, event_deadline)
        digest = _catalog_digest(trust, admin, rewind, handle, member_deadline)
        catalog = first(admin, digest, len(digest), 0, None)
        if not catalog or catalog == _INVALID:
            raise ValueError('Windows SVG system image has no installed SHA-256 catalog')
        try:
            details = _CatalogInfo(size=C.sizeof(_CatalogInfo))
            _check(info(catalog, C.byref(details), 0), 'CryptCATCatalogInfoFromContext')
            reported = Path(details.path)
            if reported.parent != folder or not _CATALOG_LEAF.fullmatch(reported.name) or not _ordinary(reported.name):
                raise ValueError('Windows SVG installed catalog is outside the pinned database directory')
            path = folder / reported.name
            held._check_parents(member_deadline)
            # Retain the catalog pin for this trust call, not the returned native context.
            with ExitStack() as cat_pin:
                opened = _open_pin(cat_pin, held.kernel, path, directory=False)
                before = _facts(held.kernel, opened, path, held.device, directory=False)
                if before[0][0] != held.volume or not 0 < before[3] <= _MAX_CAT_BYTES:
                    raise ValueError('Windows SVG installed catalog size or volume rejected')
                signer = _verify_member(trust, admin, rewind, str(member), handle, str(path), bytes(digest).hex(),
                                        None, member_deadline, signer_pins=signer_pins)
                if _facts(held.kernel, opened, path, held.device, directory=False) != before:
                    raise ValueError('Windows SVG installed catalog changed during verification')
            return signer
        finally:
            _check(release(admin, catalog, 0), 'CryptCATAdminReleaseCatalogContext')

    try:
        yield verify
    finally:
        _check(trust.CryptCATAdminReleaseContext(admin, 0), 'CryptCATAdminReleaseContext (installed system catalog)')


def _verify_scope_catalogs(pins, held, system32, catalogs, deadline):
    catroot = system32 / 'CatRoot'
    folder = catroot / ('{' + _DRIVER.upper() + '}')
    paths = tuple(folder / row[1] for row in catalogs)
    if any(len(str(path)) >= 260 for path in paths):
        raise ValueError('Windows SVG system catalog path exceeds its bound')
    for parent in (catroot, folder):
        _scope_time(deadline)
        held._parent(pins, parent)
    held._check_parents(deadline)
    trust = _trust_api()
    rewind = _windows_api('SetFilePointerEx', _B, _H, C.c_longlong, _P, _D)
    admin, remaining = _H(), _MAX_CATS_BYTES
    _scope_time(deadline)
    _check(trust.CryptCATAdminAcquireContext2(C.byref(admin), _guid(_DRIVER), 'SHA256', None, 0),
           'CryptCATAdminAcquireContext2 (system catalog)')
    if not admin.value or admin.value == _INVALID:
        raise ValueError('Windows SVG system catalog admin handle invalid')
    try:
        for (_, _, expected, pin), (member, handle, facts, _), path in zip(catalogs, held.leaves, paths):
            _scope_time(deadline)
            with ExitStack() as catalog_pin:
                cat_handle = _open_pin(catalog_pin, held.kernel, path, directory=False)
                before = _facts(held.kernel, cat_handle, path, held.device, directory=False)
                if before[0][0] != held.volume or not 0 < before[3] <= min(remaining, _MAX_CAT_BYTES):
                    raise ValueError('Windows SVG system catalog volume or size rejected')
                remaining -= before[3]
                if _facts(held.kernel, handle, member, held.device, directory=False) != facts:
                    raise ValueError('Windows SVG system catalog member metadata changed')
                _verify_member(trust, admin, rewind, str(member), handle, str(path), expected, pin, deadline)
                if (_facts(held.kernel, cat_handle, path, held.device, directory=False) != before
                        or _facts(held.kernel, handle, member, held.device, directory=False) != facts):
                    raise ValueError('Windows SVG system catalog or member metadata changed')
    finally:
        _check(trust.CryptCATAdminReleaseContext(admin, 0), 'CryptCATAdminReleaseContext (system catalog)')
    held._check_parents(deadline)
