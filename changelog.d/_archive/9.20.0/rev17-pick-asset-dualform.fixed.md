# 修复 --fetch-release 在真实 release 上的 AttributeError（真下载 E2E 抓出）
latest_release_assets 已把 API JSON 转为 ReleaseAsset 列表，pick_asset 却按 dict 取值 → AttributeError。统一为双形态兼容（dict/ReleaseAsset），单测 17 例 + 真下载 cli/cli v2.104.0（15.4MB MSI 签名与字节数一致）验证。
@verify 真下载 E2E：gh_2.104.0_windows_amd64.msi 15425536 字节 + OLE 签名 D0CF11E0A1B11AE1；test_rev17_release_fetch 17 例绿
