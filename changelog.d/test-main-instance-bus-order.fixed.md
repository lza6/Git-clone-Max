# test_main 注入 instance_bus fake，修复单跑顺序依赖（B 批懒导入撞 fake single_instance 缺 _pid_alive）
@verify tests.test_main 单跑 6 例 OK（修复前单跑必红）
