# 跨设备适配与性能重塑：L 档真正生效 / 无障碍命名 / 键盘可达 / 骨架与断网指示
`wide` 断点此前是死枚举（`layout_mode()` 会返回它，但全库只消费 `compact`/`unsupported`）——现在 1440×1000 以上进度表行高 26→28（触摸屏取 34），管理页列宽按档位分配；断点语义收敛成 XS/S/M/L 四档纯函数 `layout.breakpoint_name()`
多显示器：此前全库 0 处 `screenChanged`，副屏拔掉后最小尺寸与 DPI 规则不重算——现在监听 `primaryScreenChanged`/`screenAdded`/`screenRemoved` 按新屏重算并夹取窗口，几何串追加 `@屏名`，上次那块屏不在了回主屏并记 WARN
无障碍：14 个主操作里 11 个没有 `accessibleName`，`QLineEdit` 34/36、`QSpinBox` 19/19、`QComboBox` 8/8 读屏只报「编辑框」——现在主操作 100% 有名字+描述，输入控件按 buddy 标签/相邻标签/tooltip 推导，缺名控件 142→7；新增 `scripts/a11y_audit.py` 棘轮
键盘：此前 `setTabOrder` 与 `keyPressEvent` 全库 0 处，Tab 走的是控件创建顺序（会从页底跳到页顶）——现在每页有显式视觉顺序链，`F6` 循环三页签并把焦点交给新页第一个可操作控件
焦点可见：`:focus` 规则从 9 类控件扩到 13 类（补 复选框/单选钮/工具按钮/列表），用 outline 而非 border（border 会改变控件几何，焦点移动时等于一次布局抖动）
反馈：点「开始」到第一条结果之间是**完全空白**的表格——现在用 QPainter 画 3 行骨架占位（不建任何子控件）；最近 3 次失败全为网络类时齿条右端亮「断网」标记（几何形状编码，不用字符，微软雅黑缺字形会变 tofu），恢复即消、不弹窗
表格行 hover 与选中底色此前在两张表上都不存在——现在 hover 用 `panel2` 提亮、选中用 accent 半透明叠加（深底 18% / 浅底 12%）
管理页定宽列此前是 5 个硬编码宽度、无持久化、无自适应下限——现在按档位三档（默认/窄窗下限/L 档偏宽）并持久化到新增的 `settings.ui_state`
设计 token：`spacing.xl/2xl/inset/section`、`row.compact`、`radius.chip` 全库零消费 → 删除；`row.manage` 接进管理表、`row.touch` 与 `row.wide` 为新增档位。守卫断言每个 token 叶子要么有消费点要么登记为刻意不提供
动效时长 220/180/600 此前散在三处硬编码 → 收进 `ui/motion.py`，守卫禁止 UI 内出现裸 `setDuration(数字)`
历史「查看」按钮此前用 `style.drawControl(CE_PushButton)` 画原生按钮，深色主题下是浅灰方块 → 改 QPainter 自绘并走 `tr()`
空态三态统一：管理页此前是裸 `QLabel`、筛选到空时整片空白 → 走 `empty_state` 工厂并区分「没有数据」与「筛选无结果」
i18n：新增 AST 棘轮（未走 `tr()` 的中文字面量 1517→1426，计数只降不升），本批迁移 UI 可见 sink 上的 70 处文案并补全中英字典
工具：新增 `scripts/bench_startup.py` / `bench_resize.py`（启动分段与 8 尺寸×3 字号重排，落 `evidence/`）与 `scripts/pixel_diff.py`（截图阈值比对 + 2px 位移反向自检），截图审计从「只存图」变成 `--compare` 能报警
小屏「下载位置 / 克隆模式 / 层数」被裁到只剩 6–23px、且顶部区连 1920×1080 都要滚动 → 三个控件移出滚动区成常驻行；连带把折叠阈值按新布局逐 10px 重扫（580/600/620 → 720/740/760），splitter 在装不下时优先保常驻区，折叠态下进度表/批次进度条/进度区工具行/结论条依次让位。实测 9 尺寸 × 3 字号 + 900×480 共 28 组合**零裁切**
重复点「开始」时，被断点续传预检跳过的仓库不产生终态 → 行永远「等待中」、上次耗时被抹成「—」、通知「成功 0」、状态栏一直「并行同步中」。现在引擎用 `skipped` 信号回报（CLI 契约不变），行到「已跳过」并写明原因，小结与通知报「· 跳过 N」，`launched==0` 不再用「并行同步中」覆盖小结
键盘可达再修两处真断点：`QPlainTextEdit` 把 Tab 当缩进、`QTableView` 把 Tab 吃在单元格间 —— 声明的 tab 链原来在地址框和表格处不可达；现在 Tab 真的会离开它们（新增按真实按键的断言），F6 也已进帮助页的快捷键表
无障碍命名口径修正：`QSpinBox.text()`（当前数值）与 `QLineEdit.text()`（用户输入）此前被当成"有名字"，导致棘轮对新增无标签输入框失明；现在只有按钮类才用 `text()` 兜底，下载位置/克隆模式/浅克隆深度三个控件显式命名
零幸存者批次（全部被跳过）此前不留行 → 根因是 `install_row` 只用 `setItem`（对不存在的行是静默空操作）；现在预填前先建行，用户能看到「某某 / 已跳过 / 已完成，跳过（…）」
引擎 `_mark_done` 的 key 口径统一为 `owner/repo`（与断点续传预检一致），不再往 `progress.json` 写 `owner__repo` 的污染记录
骨架跟随窗口 resize；`motion.EASING_NAME` 改为真正生效；断网标记在新批次开始时清零
@verify tests/test_rev14_adapt(15) + test_rev14_a11y(8) + test_rev14_keyboard(8) + test_rev14_feedback(18) + test_rev14_pixel(4) + 快集 2120 例（除 2 个既有版本/日志同步红外全绿）+ ruff/mypy/doc_drift 0/i18n OK
