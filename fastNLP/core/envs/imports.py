import platform
import os
import operator


from fastNLP.core.envs.env import FASTNLP_BACKEND
from fastNLP.core.envs.utils import _module_available, _compare_version


SUPPORT_BACKENDS = ['torch', 'paddle', 'jittor']
backend = os.environ.get(FASTNLP_BACKEND, 'all')
if backend == 'all':
    need_import = SUPPORT_BACKENDS
elif ',' in backend:
    need_import = list(map(str.strip, backend.split(',')))
else:
    need_import = [backend]


_IS_WINDOWS = platform.system() == "Windows"
_NEED_IMPORT_FAIRSCALE = not _IS_WINDOWS and _module_available("fairscale.nn") and 'torch' in need_import
_NEED_IMPORT_TORCH = _module_available("torch") and 'torch' in need_import
_NEED_IMPORT_JITTOR = _module_available("jittor") and 'jittor' in need_import
_NEED_IMPORT_PADDLE = _module_available("paddle") and 'paddle' in need_import

_TORCH_GREATER_EQUAL_1_8 = _NEED_IMPORT_TORCH and _compare_version("torch", operator.ge, "1.8.0")