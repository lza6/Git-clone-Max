# 诊断包导出失败改走三段式错误对话框（U-18 收口）
diag_dialog 最后一处 QMessageBox.warning(str(exc)) 迁 error_dialog.show_error，与全库 6 处同口径。
@verify test_g74_ui_layout + test_g69_regressions 全绿；grep str(exc)=0
