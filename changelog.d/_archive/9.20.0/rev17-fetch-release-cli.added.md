# CLI 新增 --fetch-release：下载 GitHub 最新 Release 本机平台附件（断点续传）
eget 资产匹配（OS/arch 正则+anti 排除+中性资产兜底）+ web-crawler 断点续传语义（.part+Range+206 偏移校验+416 收尾+本地 OSError 不重试）。--json 输出结构化结果，退出码 0/1/2 与契约一致。
@verify tests/test_rev17_release_fetch 8 例（含 darwin 误中 win 的真 bug 回归）+ tests.test_g56_cli_json 全绿
