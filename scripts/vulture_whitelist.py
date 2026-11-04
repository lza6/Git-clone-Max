"""vulture 白名单（G84-14）。

**为什么是 Python 模块而不是文本清单**：vulture 会把这里出现的名字当作"被使用"。
写成可被解释器解析的 Python，好处是**符号一旦消失，白名单自己就会报错**
（`vulture` 会对未定义名字给 warning，`ruff`/`python -m py_compile` 也会红），
豁免因此不会悄悄腐烂 —— 这正是 vulture 官方推荐的用法。

只登记"静态分析看不见、但运行时确实被用"的符号：
- Qt 槽/信号按**字符串**连接（`clicked.connect(self.foo)` 中 foo 由 Qt 反射调用）；
- 被 `getattr(obj, "名字")` 动态访问的钩子；
- 供外部/打包使用的入口。

新增条目必须写一句理由（`tests/test_g84_quality_gates.py` 会检查不是空行堆）。
"""
from __future__ import annotations

# 入口点：`python -m gcm.mcp_server` 由解释器调用，不被 import
import gcm.mcp_server  # noqa: F401

# 供 agent/外部脚本读取的契约符号
from gcm import __changelog__, __version__  # noqa: F401

__all__ = ["__changelog__", "__version__"]
