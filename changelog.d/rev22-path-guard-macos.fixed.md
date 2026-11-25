# 删除护栏 macOS 符号链接缺口修复（/etc、/tmp、/var 指向 /private/*，resolve 后顶层系统目录被放行）
@verify tests.test_b3_path_guard 13 例（含 mock resolve 跨平台用例）+ CI macOS
