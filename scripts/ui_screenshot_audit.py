"""UI 截图审计（离屏）：多分辨率/多主题/多页签/关键对话框。

用法：
    QT_QPA_PLATFORM=offscreen python scripts/ui_screenshot_audit.py [out_dir]
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QCoreApplication, QTimer  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtGui import QFont, QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
# 离屏平台不会自动解析系统 CJK 字体；显式注册微软雅黑，否则截图全是 tofu。
for _font in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
    try:
        QFontDatabase.addApplicationFont(_font)
    except Exception:
        pass
try:
    app.setFont(QFont("Microsoft YaHei UI", 9))
except Exception:
    pass

from gcm.db.repo_db import Database  # noqa: E402
from gcm.db.settings import SettingsStore  # noqa: E402
from gcm.models import (  # noqa: E402
    RepoSpec, SyncAction, SyncResult, SyncStatus,
)

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "reports" / "ui-audit")
OUT.mkdir(parents=True, exist_ok=True)

REPOS = [
    ("vercel-labs", "skills", "github.com"),
    ("anthropics", "skills", "github.com"),
    ("microsoft", "azure-skills", "github.com"),
    ("remotion-dev", "skills", "github.com"),
    ("slidevjs", "slidev", "github.com"),
    ("openmeterio", "openmeter", "github.com"),
    ("gitlab-org", "gitlab-runner", "gitlab.com"),
    ("gitee-org", "demo-repo", "gitee.com"),
    ("codeberg-org", "forgejo", "codeberg.org"),
    ("bitbucket-org", "pipeline-tools", "bitbucket.org"),
    ("selfhosted", "internal-tools", "git.corp.example"),
    ("owner-with-a-very-long-name", "repo-with-an-even-longer-name-for-layout-testing", "github.com"),
] * 2


def _seed(db: Database, root: Path) -> list[RepoSpec]:
    specs = []
    for i, (owner, repo, host) in enumerate(REPOS):
        spec = RepoSpec(owner=owner, repo=repo,
                        url_https=f"https://{host}/{owner}/{repo}.git",
                        folder_name=f"{owner}__{repo}")
        rid = db.upsert_repo(spec, str(root / spec.folder_name), host=host)
        db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.CLONED,
                            "克隆完成", "", commits=i % 4)
        specs.append(spec)
    return specs


def _pump(ms: int = 120) -> None:
    """真实等待 + 事件循环（QTimer 去抖/布局需要时间真正流逝）。"""
    QCoreApplication.processEvents()
    if ms:
        QTest.qWait(int(ms))


def _save(widget, name: str) -> str:
    _pump(100)
    path = OUT / f"{name}.png"
    ok = widget.grab().save(str(path))
    print(("OK " if ok else "FAIL ") + str(path))
    return str(path)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="gcm_ui_audit_"))
    db = Database(tmp / "repos.db")
    specs = _seed(db, tmp / "clones")
    store = SettingsStore(tmp / "settings.json")
    settings = store.load()
    settings.download_dir = str(tmp / "clones")
    settings.first_run_done = True
    settings.checklist_prompted = True
    settings.minimize_to_tray = False
    settings.animations = False
    store.save(settings)

    from gcm.ui.main_window import MainWindow
    win = MainWindow(data_dir=tmp, db=db, settings=store, start_hidden=False)
    win.resize(1440, 900)
    win.show()
    _pump(250)

    # 填充进度表
    win.progress_panel._prepare_table(len(specs))
    statuses = [SyncStatus.SUCCESS, SyncStatus.SUCCESS, SyncStatus.FAILED,
                SyncStatus.RUNNING, SyncStatus.CONFLICT, SyncStatus.SKIPPED,
                SyncStatus.CANCELLED]
    for i, spec in enumerate(specs):
        win.progress_panel._add_table_row(i, spec)
        st = statuses[i % len(statuses)]
        res = SyncResult(spec=spec, status=st, action=SyncAction.CLONED,
                         message=("克隆完成" if st == SyncStatus.SUCCESS else
                                  "Connection timed out" if st == SyncStatus.FAILED else
                                  "本地改动冲突" if st == SyncStatus.CONFLICT else
                                  "已取消" if st == SyncStatus.CANCELLED else
                                  "已是最新"),
                         detail="mock detail for layout audit", commits=i % 4)
        try:
            win.progress_panel._on_worker_result(i, res)
        except Exception:
            pass
        win.progress_panel._progress_values[i] = (i * 13) % 100

    sizes = [(1920, 1080), (1440, 900), (1366, 768), (1280, 800), (1024, 768)]
    themes = ["deep", "light", "nord", "hc"]
    for theme in themes:
        win.settings.theme = theme
        from gcm.ui import theme as _th
        _th.apply_theme(theme)
        win.setStyleSheet(win._themed_qss())
        _pump(80)
        for w, h in sizes:
            win.resize(w, h)
            try:
                win._apply_layout_mode()
            except Exception:
                pass
            _pump(160)
            _save(win, f"download_{theme}_{w}x{h}")

    # 页签（默认主题恢复到 deep）
    win.settings.theme = "deep"
    from gcm.ui import theme as _th
    _th.apply_theme("deep")
    win.setStyleSheet(win._themed_qss())
    win.resize(1440, 900)
    _pump(90)
    for idx, name in ((0, "tab_download"), (1, "tab_manage"), (2, "tab_settings")):
        win.tabs.setCurrentIndex(idx)
        _pump(180)
        _save(win, f"{name}_1440x900")
    # 设置页滚动到底
    try:
        sb = win.settings_scroll.verticalScrollBar()
        sb.setValue(sb.maximum())
        _pump(80)
        _save(win, "tab_settings_bottom_1440x900")
        sb.setValue(0)
    except Exception:
        pass
    # 管理页小屏
    win.tabs.setCurrentIndex(1)
    win.resize(1024, 768)
    try:
        win._apply_layout_mode()
    except Exception:
        pass
    _pump(180)
    _save(win, "tab_manage_1024x768")
    # 设置页小屏（顶部 + 底部）
    win.tabs.setCurrentIndex(2)
    win.resize(1024, 768)
    _pump(160)
    _save(win, "tab_settings_1024x768")
    try:
        sb2 = win.settings_scroll.verticalScrollBar()
        sb2.setValue(sb2.maximum())
        _pump(80)
        _save(win, "tab_settings_bottom_1024x768")
        sb2.setValue(0)
    except Exception:
        pass

    # 关键对话框
    try:
        from gcm.app.retry_queue import RetryQueue
        from gcm.ui.retry_panel import RetryQueueDialog
        q = RetryQueue(path=tmp / "retry_queue.json", enabled=True, max_retries=1,
                       jitter=lambda d: d)
        for s in specs[:3]:
            q.record_failure(s, "Connection timed out")
        q.record_failure(specs[3], "Authentication failed")
        q.record_failure(specs[4], "No space left on device")
        # 同一仓库两次网络失败 → max_retries=1 触发 DLQ
        q.record_failure(specs[5], "Connection timed out")
        q.record_failure(specs[5], "Connection timed out")
        dlg = RetryQueueDialog(owner=win, parent=win)
        dlg.panel._explicit_queue = q
        dlg.show()
        dlg.panel.refresh()
        _save(dlg, "dialog_retry_queue")
        dlg.panel.ck_show_dropped.setChecked(True)
        dlg.panel.refresh()
        _save(dlg, "dialog_retry_queue_dlq")
        dlg.close()
    except Exception as e:
        print("retry dialog fail:", e)

    try:
        from gcm.app.event_bus import EventBus, EventType
        from gcm.ui.event_panel import EventStreamDialog
        bus = EventBus()
        for i in range(12):
            bus.publish(EventType.TASK_RESULT, {"index": i, "key": f"o/r{i}",
                                                "status": "success" if i % 3 else "failed",
                                                "action": "cloned", "message": "克隆完成"})
        bus.publish(EventType.TASK_ERROR, {"key": "o/r1", "category": "network",
                                           "title": "网络连接失败", "action": "检查网络后重试"})
        dlg = EventStreamDialog(bus=bus, parent=win)
        dlg.resize(860, 520)
        dlg.show()
        dlg.panel.refresh()
        _save(dlg, "dialog_event_stream")
        dlg.close()
    except Exception as e:
        print("event dialog fail:", e)

    try:
        from gcm.ui.failure_panel import FailureDialog
        # 造几条失败记录
        import json
        (tmp / "progress.json").write_text(json.dumps({
            "finished": [], "in_progress": {},
            "failed": [
                {"key": "a/b", "message": "Connection timed out"},
                {"key": "c/d", "message": "Authentication failed"},
                {"key": "e/f", "message": "No space left on device"},
                {"key": "g/h", "message": "invalid path 'a:b'"},
            ]}, ensure_ascii=False), encoding="utf-8")
        win.progress_path = tmp / "progress.json"
        dlg = FailureDialog(owner=win, parent=win)
        dlg.show(); dlg.refresh()
        _save(dlg, "dialog_failure")
        dlg.close()
    except Exception as e:
        print("failure dialog fail:", e)

    try:
        from gcm.ui.diag_dialog import DiagReportDialog
        from gcm import __version__ as _ver
        body = f"版本 {_ver}\nGit OK\n网络 OK"
        dlg = DiagReportDialog("环境体检", body, f"版本 {_ver}\nGit OK", parent=win,
                               data_dir=tmp)
        dlg.resize(760, 520); dlg.show()
        _save(dlg, "dialog_diag")
        dlg.close()
    except Exception as e:
        print("diag dialog fail:", e)

    try:
        from gcm.ui.onboarding import OnboardingDialog
        dlg = OnboardingDialog(parent=win, default_dir=str(tmp / "clones"))
        dlg.resize(560, 380); dlg.show()
        _save(dlg, "dialog_onboarding")
        dlg.close()
    except Exception as e:
        print("onboarding fail:", e)

    try:
        from gcm.ui.statistics_dialog import StatisticsDialog
        dlg = StatisticsDialog(db=db, parent=win)
        dlg.resize(860, 720); dlg.show()
        _pump(120)
        _save(dlg, "dialog_statistics")
        dlg.close()
    except Exception as e:
        print("statistics fail:", e)

    try:
        from gcm.ui.help_dialog import HelpDialog
        dlg = HelpDialog(parent=win)
        dlg.show()
        _pump(120)
        _save(dlg, "dialog_help")
        dlg.close()
    except Exception as e:
        print("help dialog fail:", e)

    try:
        from gcm.ui.checklist import ChecklistDialog, ITEMS
        status = {k: (i % 2 == 0) for i, (k, _t, _d, _a) in enumerate(ITEMS)}
        dlg = ChecklistDialog(status, parent=win)
        dlg.show()
        _pump(120)
        _save(dlg, "dialog_checklist")
        dlg.close()
    except Exception as e:
        print("checklist dialog fail:", e)

    try:
        from gcm.ui.repo_detail_dialog import RepoDetailDialog
        row = db.list_repos()[0]
        dlg = RepoDetailDialog(row, db.history(int(row["id"]), 20), parent=win)
        dlg.show()
        _pump(120)
        _save(dlg, "dialog_repo_detail")
        dlg.close()
    except Exception as e:
        print("repo detail fail:", e)

    try:
        from gcm.ui.local_repos_dialog import LocalReposDialog
        dlg = LocalReposDialog(parent=win, root_paths=[str(tmp / "clones")], db=db)
        dlg.show()
        _pump(150)
        _save(dlg, "dialog_local_repos")
        dlg.close()
    except Exception as e:
        print("local repos fail:", e)

    try:
        win.close()
    except Exception:
        pass
    db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("OUT:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
