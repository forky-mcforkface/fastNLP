__all__ = [
    'Metric'
]

from abc import abstractmethod

from typing import Union
import functools
from contextlib import contextmanager
import numpy as np

from fastNLP.core.metrics.backend import Backend, AutoBackend
from fastNLP.core.metrics.element import Element


class Metric:
    def __init__(self, backend: Union[str, Backend, None] = 'auto', aggregate_when_get_metric: bool = True):
        """

        :param str backend: 目前支持四种类型的backend, [torch, paddle, jittor, auto]。其中 auto 表示根据实际调用 Metric.update()
            函数时传入的参数决定具体的 backend ，大部分情况下直接使用 auto 即可。
        :param bool aggregate_when_get_metric: 在计算 metric 的时候是否自动将各个进程上的相同的 element 的数字聚合后再得到metric，
            当 backend 不支持分布式时，该参数无意义。
        """
        self.backend = AutoBackend(backend)
        self._updated = False
        self.get_metric = self._sync_get_metric(self.get_metric)
        self.update = self._wrap_update(self.update)
        self.reset = self._wrap_auto_reset_elements(self.reset)
        self.aggregate_when_get_metric = aggregate_when_get_metric
        self._cannot_change_element = False
        self._elements = {}

    @property
    def elements(self) -> dict:
        return self._elements

    def register_element(self, name, value: float = 0, aggregate_method=None, backend='auto') -> Element:
        """
        注册一个 element 对象，注册之后便可以通过在 Metric 中直接通过 self.{name} 进行调用，可以认为该对象即为对应 backend 的
            tensor 直接进行加减乘除计算即可。
        注意：如果想使得该 metric 可自动扩展到多卡的情况，请一定申明 aggregate_method 。

        :param name: 当前 element 的名字，注册后，在 Metric 中可以通过 self.{name} 访问该变量。
        :param value: 初始化的值。在调用 Metric.reset() 方法时也将自动设置为该值
        :param aggregate_method: 如何聚合多卡上的结果，如果为单卡执行，该值无意义。
        :param backend: 使用的 backend 。Element 的类型会根据 backend 进行实际的初始化。例如 backend 为 torch 则该对象为
            Torch.tensor ； 如果backend 为 paddle 则该对象为 paddle.tensor ；如果 backend 为 jittor , 则该对象为 jittor.Var 。
            一般情况下直接默认为 auto 就行了，fastNLP 会根据实际调用 Metric.update() 函数时传入的参数进行合理的初始化，例如当传入
            的参数中只包含 torch.Tensor 这一种 tensor 时（可以有其它非 tensor 类型的输入）则认为 backend 为 torch ；只包含
             jittor.Var 则认为 backend 这一种 tensor 时（可以有其它非 tensor 类型的输入）则认为 backend 为 jittor 。如果没有检测
            到任何一种 tensor ，就默认使用 float 类型作为 element 。
        :return: 注册的 Element 对象
        """
        if backend == 'auto':
            backend = self.backend
        else:
            backend = AutoBackend(backend)

        assert name is not None and name not in self.elements

        element = Element(name=name, value=value, aggregate_method=aggregate_method, backend=backend)
        self.elements[name] = element
        setattr(self, name, element)
        return element

    def reset(self):
        """
        如果有非 element 的对象需要 reset 的时候，在本方法中写下非 element 的reset 方式。注册的 element 对象会自动 reset 为初始值。

        """
        pass

    def _wrap_auto_reset_elements(self, reset):
        @functools.wraps(reset)
        def _wrap_reset(*args, **kwargs):
            self._updated = False
            for ele in self.elements.values():
                ele.reset()
            reset(*args, **kwargs)

        return _wrap_reset

    def _sync_get_metric(self, get_metric):
        @functools.wraps(get_metric)
        def _wrap_get_metric(*args, **kwargs):
            assert self._updated, f"You have to call `{self.__class__.__name__}` update() function before calling " \
                                  f"get_metric()."
            with self.sync(recover=True, aggregate=self.aggregate_when_get_metric):
                results = get_metric(*args, **kwargs)
            return results

        return _wrap_get_metric

    def __setattr__(self, key, value):
        if hasattr(self, '_cannot_change_element') and self._cannot_change_element is True:
            if key in self.elements and value is not self.elements[key]:
                raise RuntimeError(f"self.`{key}` is an element, cannot assign to a new value:{value}")
        object.__setattr__(self, key, value)

    def _wrap_update(self, update):
        @functools.wraps(update)
        def _wrap_update(*args, **kwargs):
            self.check_backend(*args, **kwargs)
            self._cannot_change_element = True
            self._updated = True
            return update(*args, **kwargs)

        return _wrap_update

    def check_backend(self, *args, **kwargs):
        if not self.backend.is_specified():
            _args = []
            for arg in args:
                _args.append(arg)
            for arg in kwargs.values():
                _args.append(arg)
            self.backend.choose_real_backend(_args)

    @contextmanager
    def sync(self, recover=True, aggregate=False):
        """
        在这个上下文下， metric 会自动先同步需要同步操作的 element 。当 recover 为 True 时，在退出环境的时候，会重新将 element 的
            值恢复到计算前的值。

        """
        keep_value = {}
        if aggregate:
            for name, element in self.elements.items():
                # 保存过去的值
                keep_value[name] = element.get_scalar()
                # 聚合结果
                element.aggregate()

        yield

        if recover and aggregate:
            for name, element in self.elements.items():
                # 恢复结果
                if name in keep_value:
                    element.fill_value(value=keep_value.get(name))

    @abstractmethod
    def update(self, *args, **kwargs):
        raise NotImplementedError()

    @abstractmethod
    def get_metric(self) -> dict:
        raise NotImplementedError()

    def set_auto_aggregate_when_get_metric(self, flag: bool):
        """
        设置是否在 get_metric 的时候自动 aggregate

        """
        self.aggregate_when_get_metric = flag

    def __getattr__(self, name: str) -> Element:
        if 'elements' in self.__dict__:
            elements = self.__dict__['elements']
            if name in elements:
                return elements[name]
        raise AttributeError("`{}` object has no attribute `{}`".format(type(self).__name__, name))

    def tensor2numpy(self, tensor) -> np.array:
        """
        将tensor向量转为numpy类型变量

        :param tensor:
        :return:
        """
        return self.backend.tensor2numpy(tensor)

    def to(self, device):
        """
        将所有的 element 变量移动到 device 设备上

        :param device:
        :return:
        """
        for element in self.elements.values():
            element.to(device)