# CODE_SIGNING_POLICY — Git-clone-Max 签名与分发策略

> Rev17 Batch D（可做部分）：证书未到位前的政策基线。签名落地后按 §3 流程替换本文件中的占位。
> 状态：**未签名**（SmartScreen 会拦「未知发布者」；这是当前分发链路的已知限制，不是缺陷）。

## 1. 现状（诚实清单）

| 项 | 现状 | 证据 |
|----|------|------|
| exe/zip 代码签名 | 未签名 | 无证书；`installer.iss` 无 `SignTool` 配置 |
| winget 清单 | 生成链在位（`scripts/gen_winget_manifest.py`），sha256 与 dist 产物一致 | `packaging/winget/*.yaml`；`test_g80_winget_manifest` |
| scoop bucket | 未建 | 骨架见 §4 |
| 自更新 | 只提示不覆盖安装（`app/updater.py` 顶部声明） | 「发现新版 → 跳转下载」；避免 UAC/杀软/进程锁文件坑 |
| 假绿防线 | CI 绿 ≠ 本机 exe 能起来：发版前必须本机 `health_check.py --json` | `docs/RELEASE_CHECKLIST.md` |

## 2. 证书到位后的签名流程（占位，待填）

1. 证书类型：OV（组织验证）起步 —— EV 会直接过 SmartScreen 信誉但价格高一个量级，
   个人开发者项目 OV + 累积下载量是性价比路线。
2. 签名工具：`signtool sign /fd SHA256 /tr <TSA_URL> /td SHA256 <exe>`（时间戳必须，防证书过期后签名失效）。
3. 接线点：`Git-clone-Max.spec` 的 PyInstaller 流程之后、`build_portable_zip.py` 打 zip 之前 ——
   **zip 里的 exe 必须是已签名的**（用户解压拿到的是签名件）。
4. winget 清单在签名后重跑 `gen_winget_manifest.py`（sha 会变）。
5. `installer.iss` 加 `SignTool` 段；inno signature 检查进 `health_check.py`。

## 3. 发布门禁（签名前后一致，不变）

1. `PyInstaller --noconfirm Git-clone-Max.spec`
2. `build_portable_zip.py`
3. `health_check.py --json`（9 项 OK；smoke_ms 中位数 ≤3500）
4. `gen_winget_manifest.py`（在**最终二进制**上算 sha）
5. `test_g80_winget_manifest` 绿
6. bump → changelog build → tag → push → publish_release

## 4. scoop bucket 骨架（证书到位后即可激活）

```text
bucket/
  lza6-git-clone-max.json    # scoop manifest：version + url 指向 Release zip + hash
```

生成脚本（签名落地后新增，本文件只登记设计）：读 `evidence/health_*.json` 的
`zip_sha256` + `__version__`，产出 manifest；校验与 winget 同源（同一份 zip、同一 sha）。

## 5. 自更新（现状正确，勿推翻）

`updater.py` 的「只提示不覆盖安装」是**已验证的设计决策**（顶部 docstring 写明理由）：
签名 + 累积信誉到位前，自动覆盖安装会撞 SmartScreen/杀软/在跑进程锁三件事。
将来若做静默自更新，蓝本是 apex-download-manager 的 winget 升级路径，不是自写安装器。
