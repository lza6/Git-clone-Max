import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
NEW = "9.4.5"


def sub(path, pairs):
    p = pathlib.Path(path)
    t = p.read_text(encoding="utf-8")
    for a, b in pairs:
        assert a in t, f"{path}: {a!r}"
        t = t.replace(a, b, 1)
    p.write_text(t, encoding="utf-8")
    print("updated", path)


sub("gcm/__init__.py", [('__version__ = "9.4.4"', f'__version__ = "{NEW}"')])
sub("pyproject.toml", [('version = "9.4.4"', f'version = "{NEW}"')])
sub("Git-clone-Max.spec", [
    ("'FileVersion', '9.4.4'", f"'FileVersion', '{NEW}'"),
    ("'ProductVersion', '9.4.4'", f"'ProductVersion', '{NEW}'"),
    ("filevers=(9, 4, 4, 0)", "filevers=(9, 4, 5, 0)"),
    ("prodvers=(9, 4, 4, 0)", "prodvers=(9, 4, 5, 0)"),
])
sub("packaging/installer.iss", [
    ("Git-clone-Max-setup-9.4.4.exe", f"Git-clone-Max-setup-{NEW}.exe"),
    ('#define MyAppVersion "9.4.4"', f'#define MyAppVersion "{NEW}"'),
])
sub("docs/agent-contract.md", [
    ("适用版本：v9.4.4", f"适用版本：v{NEW}"),
    ('"version": "9.4.4"', f'"version": "{NEW}"'),
    ('"version":"9.4.4"', f'"version":"{NEW}"'),
])

p = pathlib.Path("gcm/__init__.py")
t = p.read_text(encoding="utf-8")
anchor = '    "9.4.4:'
entry = (
    '    "9.4.5: G69-18 审查整改（独立对抗式审查 Request Changes 全部关闭）- '
    '①**引用环真正的最后一环**：`_layout_timer` 用 lambda 捕获 self，'
    '一旦 show() 由 resizeEvent 重新成环 —— 此前「根因已消除」对生产路径不成立、'
    '守卫只覆盖从不 show() 的窗口（假绿）。改绑定方法后 show() 之后仍按引用计数释放；'
    '**并存结论：恢复正常 GC 后全量连跑 3 次全绿，移除 runner 的 gc.disable() 安全网**。'
    '②引导清单只在首次提示（新增 settings.checklist_prompted），且改在向导返回后触发'
    '（原 600ms 定时器会在向导的嵌套事件循环里叠着弹）；'
    '③修 `count_repos` 不存在导致「已导入仓库」项永远不完成；'
    '④僵尸字段守卫改 **AST 扫描**（原文本匹配被自己的 changelog 喂成假绿）；'
    '⑤`parse_urls` 跳过 # 注释（与校验条语义统一）；'
    '⑥校验条/清单说明改用主题语义色（原写死 Deep hex，Light 下对比度不足）+ 强制 PlainText；'
    '⑦notify 兜底改记 event=error 审计、幂等指纹纳入 URL；⑧schtasks 用例改与语言无关的判据',  # noqa: E501
    '\n'
)
i = t.index(anchor)
p.write_text(t[:i] + entry + t[i:], encoding="utf-8")
print("changelog 9.4.5 已加")

r = pathlib.Path("README.md")
rt = r.read_text(encoding="utf-8")
m = re.search(r"> \*\*当前版本：v9\.4\.4\*\*[^\n]*\n", rt)
assert m, "README 版本行未命中"
r.write_text(
    rt[:m.start()]
    + ("> **当前版本：v9.4.5**（独立审查整改：修掉引用环的**最后一环**"
       "（show() 后由布局定时器重新成环）并据此移除测试运行器的 GC 安全网；"
       "引导清单只在首次提示；「已导入仓库」项恢复可用；校验条与真实克隆链路的注释语义统一）\n")
    + rt[m.end():],
    encoding="utf-8")
print("README 已更新")
