"""Owner-only persistence for phone data. Public demo exports use ordinary I/O."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import IO


def _restrict_descriptor(descriptor: int, mode: int) -> None:
    os.fchmod(descriptor, mode)
    if sys.platform == "darwin":
        # macOS extended ACL grants are independent of POSIX mode bits.
        # Use the already validated descriptor so no pathname is followed again.
        import ctypes
        library = ctypes.CDLL(None, use_errno=True)
        library.acl_init.argtypes = [ctypes.c_int]
        library.acl_init.restype = ctypes.c_void_p
        library.acl_set_fd_np.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        library.acl_set_fd_np.restype = ctypes.c_int
        library.acl_free.argtypes = [ctypes.c_void_p]
        library.acl_free.restype = ctypes.c_int
        acl = library.acl_init(0)
        if not acl:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        try:
            if library.acl_set_fd_np(descriptor, acl, 0x00000100) != 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code))
        finally:
            library.acl_free(acl)


def _check_owner(info: os.stat_result, path: Path) -> None:
    if info.st_uid != os.geteuid():
        raise PermissionError(f"Private artifact is owned by another account: {path}")


def _private_directory(path: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        _check_owner(os.fstat(descriptor), path)
        _restrict_descriptor(descriptor, 0o700)
    finally:
        os.close(descriptor)


def _make_parents(path: Path) -> None:
    """Create missing parents privately; leave existing arbitrary ancestors alone."""
    if path.exists():
        if not path.is_dir():
            raise NotADirectoryError(str(path))
        return
    if path.is_symlink():
        raise PermissionError(f"Private directory cannot be a symlink: {path}")
    _make_parents(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _private_directory(path)


def private_directory(path: Path) -> None:
    """Create or restrict a designated application-owned directory, not its parents."""
    path = Path(path)
    if path.resolve() in {Path.cwd().resolve(), Path.home().resolve(), Path(tempfile.gettempdir()).resolve(), Path("/")}:
        raise PermissionError(f"Choose a dedicated private runtime directory: {path}")
    _make_parents(path)
    _private_directory(path)


def _check_regular(info: os.stat_result, path: Path) -> None:
    _check_owner(info, path)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PermissionError(f"Private artifact must be a regular file with one link: {path}")


def secure_private_tree(path: Path) -> None:
    """Repair old private artifacts without changing content or following links."""
    private_directory(path)
    for child in path.iterdir():
        info = child.lstat()
        if stat.S_ISDIR(info.st_mode):
            secure_private_tree(child)
        elif stat.S_ISREG(info.st_mode):
            descriptor = os.open(child, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                _check_regular(os.fstat(descriptor), child)
                _restrict_descriptor(descriptor, 0o600)
            finally:
                os.close(descriptor)
        else:
            raise PermissionError(f"Unsafe entry in private runtime directory: {child}")


def secure_runtime_roots() -> None:
    """Migrate only existing, designated CWD runtime trees; create nothing."""
    for name in ("workspace", "backups", "probe"):
        path = Path(name)
        if path.exists() or path.is_symlink():
            secure_private_tree(path)


def open_private(path: Path, *, exclusive: bool = False, binary: bool = False,
                 encoding: str = "utf-8") -> IO:
    """Validate a non-following descriptor and restrict it before truncating/writing."""
    path = Path(path)
    _make_parents(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    if exclusive:
        flags |= os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        _check_regular(os.fstat(descriptor), path)
        _restrict_descriptor(descriptor, 0o600)
        if not exclusive:
            os.ftruncate(descriptor, 0)
        return os.fdopen(descriptor, "wb" if binary else "w", **({} if binary else {"encoding": encoding}))
    except BaseException:
        os.close(descriptor)
        raise


def write_private(path: Path, content: str | bytes, *, encoding: str = "utf-8",
                  exclusive: bool = False) -> int:
    with open_private(path, exclusive=exclusive, binary=isinstance(content, bytes), encoding=encoding) as stream:
        return stream.write(content)
