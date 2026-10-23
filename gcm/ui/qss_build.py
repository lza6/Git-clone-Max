"""QSS 拼装（G75）：由色板 + token + 排版角色生成全量样式表。

从 `theme.py` 拆出（原 `_build_qss` 约 165 行，加上新的层级/齿条/页签规则会破 800 行）。

**Qt QSS 的物理边界**（决定了这里的写法）：
- 不支持 `box-shadow`：层次用 1px `glow` 内嵌高光 + 边框对比表达；
- 不支持 `transition`：hover/pressed 是即时换色，真过渡只能用 QPropertyAnimation；
- 不支持 `letter-spacing`：字距在 `typography.apply_type()` 里用 QFont 设；
- `qlineargradient` / `rgba()` 可用：大气底与半透明高光就靠它们。

**motion=False（reduced-motion）**：彻底去掉 hover/pressed 块（不是把颜色改成同色），
否则"减少动效"只是名义上的。
"""
from __future__ import annotations

from .palettes import PALETTES, TOKENS, _luminance, _on_accent_for, _shade
from .typography import FONT_BASE_PX, role_stack, type_px

_QSS_CACHE: dict[tuple, str] = {}
_CACHE_LIMIT = 32


def qss_for_theme(name: str, scale: float = 1.0, motion: bool = True) -> str:
    """按主题名生成其 QSS（未知回退 deep）。"""
    pal = PALETTES.get(name) or PALETTES["deep"]
    return qss_for_palette(pal, scale, motion)


def qss_for_palette(palette: dict, scale: float = 1.0, motion: bool = True) -> str:
    """按任意色板（可含强调色覆盖）生成 QSS。"""
    return _build_qss(palette, scale, motion)


def _build_qss(palette: dict, scale: float = 1.0, motion: bool = True) -> str:
    """由色板 + 设计 token + 排版角色生成 QSS。

    - scale 钳制 [0.8, 1.6]（与旧测试兼容）；UI 层下限 FONT_SCALE_MIN_UI 由设置页负责；
    - 按 (palette, scale, motion) 缓存 —— 拖动缩放/切换主题时避免重复拼串。
    """
    try:
        scale = float(scale)
    except Exception:
        scale = 1.0
    scale = max(0.8, min(1.6, scale))
    cache_key = (tuple(sorted((str(k), str(v)) for k, v in palette.items())),
                 round(scale, 3), bool(motion))
    cached = _QSS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    px = round(FONT_BASE_PX * scale)          # 基体（保持旧口径：round(13*scale)）
    title_px = type_px("h1", scale)           # 20 × scale
    display_px = type_px("display", scale)    # 28 × scale
    h2_px = type_px("h2", scale)              # 15 × scale
    metric_px = type_px("metric", scale)      # 18 × scale
    console_px = type_px("mono", scale)       # 12 × scale
    small_px = type_px("small", scale)        # 11 × scale
    bar_px = max(16, round(20 * scale))
    body_stack = role_stack("body")
    display_stack = role_stack("display")
    mono_stack = role_stack("mono")

    bg = palette["bg"]
    panel = palette["panel"]
    panel2 = palette["panel2"]
    border = palette["border"]
    border_strong = palette.get("border_strong") or border
    accent = palette["accent"]
    on_accent = palette.get("on_accent") or _on_accent_for(accent, palette)
    cta = palette.get("cta") or on_accent
    on_cta = palette.get("on_cta") or accent
    glow = palette.get("glow") or "rgba(255, 255, 255, 0.06)"
    err = palette["error"]
    text = palette["text"]
    dim = palette["text_dim"]
    cons = palette["console"]
    cons_fg = palette["console_fg"]

    r_card = int(TOKENS["radius"]["card"])
    r_ctl = int(TOKENS["radius"]["control"])
    r_bar = int(TOKENS["radius"]["bar"])
    r_tab = int(TOKENS["radius"]["tab"])
    r_cb = int(TOKENS["radius"]["checkbox"])
    b_def = int(TOKENS["border"]["default"])
    b_fmt = int(TOKENS["border"]["focus"])
    sp_xs = int(TOKENS["spacing"]["xs"])
    sp_sm = int(TOKENS["spacing"]["sm"])
    sp_md = int(TOKENS["spacing"]["md"])
    sp_lg = int(TOKENS["spacing"]["lg"])

    pressed = _shade(panel2, 118)
    hover_bg = _shade(panel2, 108)
    cta_hover = _shade(cta, 108) if _luminance(bg) >= 0.2 else _shade(cta, 92)
    # 按背景亮度选择交替行底色（不用魔法字符串判断主题）
    alt_bg = _shade(panel, 125) if _luminance(bg) < 0.2 else _shade(panel, 96)
    # 危险按钮改描边样式 —— 「暗红底 + 红字」在浅色主题下对比度不足
    danger_qss = (f"QPushButton#danger {{ background: transparent; "
                  f"border: {b_def}px solid {err}; color: {err}; }}")

    if not motion:
        hover_motion = ""
        pressed_motion = ""
        item_hover = ""
        tab_hover = ""
        handle_hover = ""
    else:
        hover_motion = (
            f"QPushButton:hover {{ background: {hover_bg}; border-color: {accent}; }}")
        pressed_motion = f"QPushButton:pressed {{ background: {pressed}; }}"
        item_hover = f"QListWidget::item:hover {{ background: {hover_bg}; }}"
        tab_hover = f"QTabBar::tab:hover {{ color: {text}; border-top-color: {accent}; }}"
        handle_hover = f"QSplitter::handle:hover {{ background: {accent}; }}"

    style = f"""
QWidget {{ background: {bg}; color: {text};
            font-size: {px}px; font-family: {body_stack}; }}
QMainWindow, QDialog {{ background: {bg}; }}
QWidget#rootBg {{ background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 {panel2}, stop: 1 {bg}); }}
QLabel#pageTitle {{ font-size: {title_px}px; font-weight: 700;
                    font-family: {display_stack}; color: {text}; }}
QLabel#display {{ font-size: {display_px}px; font-weight: 600;
                  font-family: {display_stack}; color: {text}; }}
QLabel#metric {{ font-size: {metric_px}px; font-weight: 600;
                 font-family: {display_stack}; color: {text}; }}
QLabel#kicker {{ font-size: {small_px}px; font-weight: 500;
                 font-family: {mono_stack}; color: {accent}; }}
QLabel#monoLabel {{ font-size: {console_px}px; font-family: {mono_stack};
                    color: {dim}; }}
QLabel#emptyHint {{ font-size: {h2_px}px; font-family: {body_stack}; color: {dim}; }}
QLabel#accent {{ color: {accent}; font-weight: 600; }}
QLabel#muted {{ color: {dim}; }}
QGroupBox {{
  border: {b_def}px solid {border}; border-top: {b_def}px solid {glow};
  border-radius: {r_card}px; margin-top: {sp_md}px; padding-top: {sp_sm}px;
  background: {panel}; font-size: {h2_px}px; font-weight: 600; color: {text};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: {sp_md}px;
                    padding: 0 {sp_xs + 2}px; color: {text}; }}
QPlainTextEdit, QTextEdit, QLineEdit, QSpinBox, QComboBox {{
  background: {panel2}; border: {b_def}px solid {border_strong};
  border-radius: {r_ctl}px; padding: {sp_sm}px;
  selection-background-color: {accent}; selection-color: {on_accent};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
  border: {b_fmt}px solid {accent};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled, QPlainTextEdit:disabled {{
  color: {dim}; background: {panel};
}}
/* 原生上下箭头在深色主题下是「深色画深色」——只剩空按钮和一条错位边线 */
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; height: 0; border: none; }}
QSpinBox::up-arrow, QSpinBox::down-arrow {{ width: 0; height: 0; image: none; }}
QPushButton {{
  background: transparent; border: {b_def}px solid {border};
  border-radius: {r_ctl}px; padding: {sp_sm}px {sp_lg}px; color: {text};
  min-height: 24px;
}}
QPushButton:focus {{ border: {b_fmt}px solid {accent}; }}
QPushButton:disabled {{ color: {dim}; border-color: {border}; background: transparent; }}
{hover_motion}
{pressed_motion}
/* G75：主操作 = 反相块（骨白/墨），不再是蓝色按钮；蓝色是 GitHub 的身份证 */
QPushButton#primary {{
  background: {cta}; border: {b_def}px solid {cta}; color: {on_cta};
  font-weight: 700; font-size: {h2_px}px;
}}
QPushButton#primary:hover {{ background: {cta_hover}; border-color: {accent}; }}
QPushButton#primary:disabled {{ background: {panel2}; color: {dim}; border-color: {border}; }}
{danger_qss}
QPushButton#danger:hover {{ background: {panel2}; border-color: {err}; }}
QTableWidget, QTableView {{
  background: {panel}; alternate-background-color: {alt_bg};
  gridline-color: {border}; border: {b_def}px solid {border};
  border-radius: {r_ctl}px;
}}
QTableWidget:focus, QTableView:focus {{ outline: {b_fmt}px solid {accent}; }}
QHeaderView::section {{
  background: {panel2}; color: {dim}; border: none;
  border-right: {b_def}px solid {border}; padding: {sp_sm}px;
  font-size: {small_px}px; font-weight: 700;
}}
QTextEdit#console {{
  background: {cons}; color: {cons_fg};
  font-family: {mono_stack}; font-size: {console_px}px;
  border: {b_def}px solid {border}; border-radius: {r_ctl}px;
}}
QTextEdit#pane {{
  background: {cons}; color: {cons_fg};
  font-family: {mono_stack}; font-size: {console_px}px;
  border: {b_def}px solid {border}; border-radius: {r_ctl}px;
}}
QProgressBar {{
  border: {b_def}px solid {border}; border-radius: {r_bar}px;
  background: {panel2}; text-align: center; color: {text};
  font-size: {small_px}px; min-height: {bar_px}px; max-height: {bar_px}px;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: {max(2, r_bar - 1)}px; }}
QStatusBar {{ color: {dim}; font-size: {small_px}px; }}
QCheckBox::indicator, QRadioButton::indicator {{
  width: 18px; height: 18px; border: {b_def}px solid {border_strong};
  border-radius: {r_cb}px; background: {panel2};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
  background: {accent}; border-color: {accent};
}}
/* 全局 QWidget 背景会让每个 QLabel/QCheckBox 在 GroupBox 底色上画出深色补丁 */
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
QScrollBar:vertical {{ background: {bg}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{
  background: {border}; border-radius: {r_bar}px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {border_strong}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: {bg}; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{
  background: {border}; border-radius: {r_bar}px; min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {border_strong}; }}
QTabWidget::pane {{ border: {b_def}px solid {border}; border-radius: {r_tab}px;
                    background: {bg}; top: -1px; }}
QTabBar::tab {{
  padding: {sp_sm}px {sp_lg}px; background: transparent; color: {dim};
  border: none; border-bottom: {b_fmt}px solid transparent;
  font-size: {h2_px}px; font-weight: 500;
}}
QTabBar::tab:selected {{
  background: {panel}; color: {text}; font-weight: 700;
  border-bottom: {b_fmt}px solid {accent};
}}
QTabBar::tab:focus {{ border-bottom: {b_fmt}px solid {accent}; }}
{tab_hover}
/* 原生分割条抓手在浅色主题下会显示成一串游离的「……」→ 改 1px 分隔线 */
QSplitter::handle {{ background: {border}; }}
QSplitter::handle:horizontal {{ width: {b_def}px; }}
QSplitter::handle:vertical {{ height: {b_def}px; }}
{handle_hover}
QListWidget {{ background: {panel}; border: {b_def}px solid {border};
               border-radius: {r_ctl}px; padding: {sp_xs}px;
               font-size: {h2_px}px; }}
QListWidget::item {{ padding: {sp_sm}px {sp_sm + 2}px;
                     border-radius: {r_cb}px; border-left: {b_fmt + 1}px solid transparent; }}
QListWidget::item:selected {{ background: {panel2}; color: {text};
                              border-left: {b_fmt + 1}px solid {accent}; }}
{item_hover}
/* 设置页左导航：编号 + 指示条（等宽编号，看着像仪器通道号） */
QListWidget#settingsNav {{ background: transparent; border: none; padding: 0; }}
QListWidget#settingsNav::item {{ padding: {sp_sm}px {sp_md}px;
                                 font-family: {mono_stack}; font-size: {px}px; }}
QToolTip {{ background: {panel2}; color: {text};
            border: {b_def}px solid {border_strong}; padding: {sp_xs}px {sp_sm}px; }}
"""
    from . import qss_validate  # 静态 QSS 校验器（未知属性/类名/括号配平）
    qss_validate.validate_qss_or_raise(style)
    if len(_QSS_CACHE) > _CACHE_LIMIT:   # 有界缓存：主题 × 缩放组合远小于 32
        _QSS_CACHE.clear()
    _QSS_CACHE[cache_key] = style
    return style


# 兼容旧导入面：调用方从本模块或 theme 拿 `FONT_BASE_PX` 都行
__all__ = ["qss_for_theme", "qss_for_palette", "_build_qss", "FONT_BASE_PX"]
