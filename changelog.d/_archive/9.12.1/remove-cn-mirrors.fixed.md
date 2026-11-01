# 移除内置国内镜像：实测比直连慢 8 倍
同一个仓库（lza6/Git-clone-Max）在干净临时目录下连续两轮实测：走内置镜像 24,836ms / 24,398ms，直连源站 3,079ms / 2,971ms —— **慢约 8 倍**。而且它默认开启，每个仓库克隆前还要先 git ls-remote 探测一轮候选（每个最多 8s）。
处置：DEFAULT_GITHUB_MIRRORS 永久清空（不是默认关——留着还会有人打开）；settings.mirror_use_presets 保留但失效（旧 settings.json 无损加载）；设置页移除「使用内置国内镜像」开关；CLI --no-mirror 保留为兼容开关。**用户自定义镜像仍支持**，「有凭据绝不走镜像」「失败回退源站」两条约束不变。
@verify tests/test_g58_mirror_failover（58 例，含 test_no_builtin_presets_at_all 钉死预设必须为空 / test_default_settings_do_not_use_mirrors）+ 实测默认 3.0–3.5s 直连
