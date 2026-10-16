# -*- coding: utf-8 -*-
"""G69-18：`GIT_CONFIG_*` 进程环境隔离 mixin。

## 为什么需要

`GitService._env()` 会**读取进程环境里已有的 `GIT_CONFIG_COUNT`**，把本次要注入的
配置**追加**在其后（git 的 `GIT_CONFIG_KEY_<n>` / `GIT_CONFIG_VALUE_<n>` 协议要求
索引连续，不能跳号也不能少项）。

因此，**凡是断言 `GIT_CONFIG_COUNT` 具体数值或断言 `KEY_0/VALUE_0` 具体内容的测试，
都隐含了「进程环境里没有任何 `GIT_CONFIG_*`」这个前提**。

而这个前提在真实环境里经常不成立：

- GitHub Actions 的 runner 会设 `GIT_CONFIG_COUNT=2`（`credential.interactive=false`
  与 `credential.guiPrompt=false`，防止 CI 里 git 弹交互提示）；
- Git for Windows 的包装脚本、其它工具的 wrapper 也会设。

不隔离就会出现**「本机绿、CI 红」的假红灯** —— 测试红是因为环境被污染，
不是产品有问题。这类断言必须显式隔离环境，而不是靠运气。

## 用法

    class MyTest(GitConfigEnvIsolation, unittest.TestCase):
        ...

mixin 的 `setUp`/`tearDown` 会调用 `super()`，可与其它 mixin 叠加。
"""
from __future__ import annotations

import os

_PREFIX = "GIT_CONFIG_"


class GitConfigEnvIsolation:
    """备份-清除-还原进程内所有 `GIT_CONFIG_*` 变量。"""

    def setUp(self):
        # 只清理 / 还原本 mixin 关心的前缀，不动其它环境变量。
        self._gitcfg_backup = {
            k: v for k, v in os.environ.items() if k.startswith(_PREFIX)
        }
        for k in [k for k in os.environ if k.startswith(_PREFIX)]:
            os.environ.pop(k, None)
        super().setUp()

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            # 清掉测试期间新增/残留的项，再精确还原进入前的快照
            # （不整份 os.environ 覆盖，避免抹掉其它测试的改动）。
            for k in [k for k in os.environ if k.startswith(_PREFIX)]:
                os.environ.pop(k, None)
            os.environ.update(self._gitcfg_backup)
