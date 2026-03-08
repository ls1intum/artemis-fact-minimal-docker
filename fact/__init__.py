"""
Test framework for C-programs. The framework implements compilation tests, input-output tests,
structural tests and grey box tests.
"""
import glob
import os
from platform import system

from clang.cindex import Config


def setup_libclang():
    """Sets the appropriate libclang source path for the current environment."""
    env_path = os.environ.get('LIBCLANG_PATH')
    if env_path:
        Config.set_library_file(env_path)
        return

    __system = system()
    if __system == 'Darwin':
        Config.set_library_path('/usr/local/opt/llvm/lib')
    elif __system == 'Linux':
        candidates = sorted(glob.glob('/usr/lib/llvm-*/lib/libclang*.so.1'), reverse=True)
        if candidates:
            Config.set_library_file(candidates[0])


setup_libclang()
