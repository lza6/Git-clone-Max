# 四轨独立复审修复：下载重试死代码/302 泄凭据/预览勾选闭环/对话框留存
claude -p 四域拆分复审抓出：①MAJOR download_asset except 顺序错误（URLError 系 OSError 子类在后→网络重试 100% 不可达）；②MAJOR 302 跨 host 重定向把 Authorization 带离 github.com（G69-21 同款）—— _SafeRedirect 剥离；③预览「执行勾选行」契约闭环（accept 后同链路 start_all）；④对话框 WA_DeleteOnClose。另有 5 项假阳性经实证驳回（记录在 acceptance-rev17.json）。
@verify test_rev17_release_fetch 20 例（含 except 顺序 AST 守卫/302 剥离单元/重试行为级 attempts=3）；快集第八轮全绿
