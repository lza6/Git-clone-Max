# main_window.__init__ 269→185 行：后台装配搬移到 ui/startup_wiring.py
看门狗/告警扫描/定时报表/队列接线/凭据提示/自动更新定时器/启动耗时七段纯搬移（不改逻辑），照 engine_factory 先例。告警接线守卫跟着搬移点走；最长函数守卫改为「必须是已登记技术债集合成员」（拆分后 qss_build._build_qss 262 行接管最长）。
@verify test_c1_wiring 5 例 + test_g69_regressions 97 例 + test_g46_theme/test_g74/test_g78 共 126 例全绿；mypy 152 文件 Success
