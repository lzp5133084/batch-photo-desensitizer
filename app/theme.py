# -*- coding: utf-8 -*-
"""界面主题：2027 商用级科技风配色与样式表"""

# 主色
BG_0 = "#070B12"
BG_1 = "#0C1320"
BG_2 = "#111A2A"
CARD = "#0F1926"
CARD_HOVER = "#14202F"
BORDER = "rgba(255,255,255,0.07)"
BORDER_STRONG = "rgba(255,255,255,0.14)"
TEXT = "#E8F0FA"
TEXT_SUB = "#8FA6C4"
TEXT_DIM = "#5F7699"
ACCENT = "#2EE6C5"      # 青绿
ACCENT_2 = "#3B82F6"    # 科技蓝
ACCENT_3 = "#A78BFA"    # 淡紫
GOLD = "#F7C948"
DANGER = "#F87171"
OK = "#34D399"

FONT_FAMILY = "Microsoft YaHei"

QSS = """
* { font-family: "%(font)s"; outline: none; }
QWidget {
    color: %(text)s;
    font-size: 13px;
}
QMainWindow, #Root {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #070B12, stop:0.45 #0A1220, stop:1 #0B0F1C);
}
#Header {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(46,230,197,0.10), stop:0.35 rgba(59,130,246,0.10), stop:1 rgba(167,139,250,0.08));
    border-bottom: 1px solid %(border)s;
}
#AppTitle { font-size: 21px; font-weight: 700; color: %(text)s; letter-spacing: 1px; }
#AppSub { font-size: 12px; color: %(text_sub)s; }
#VersionBadge {
    background: rgba(46,230,197,0.14); color: %(accent)s;
    border: 1px solid rgba(46,230,197,0.35); border-radius: 9px;
    padding: 2px 8px; font-size: 11px; font-weight: 600;
}
#SafeBadge {
    background: rgba(59,130,246,0.14); color: #7CC4FF;
    border: 1px solid rgba(59,130,246,0.30); border-radius: 9px;
    padding: 2px 8px; font-size: 11px;
}
#AuthorInfo { color: %(text_sub)s; font-size: 11px; }
#AuthorName { color: %(gold)s; font-size: 12px; font-weight: 600; }

QFrame#Card {
    background: %(card)s;
    border: 1px solid %(border)s;
    border-radius: 16px;
}
QFrame#Card:hover { border: 1px solid %(border_strong)s; }
QFrame#SubCard {
    background: rgba(255,255,255,0.025);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 12px;
}
#CardTitle { font-size: 14px; font-weight: 700; color: %(text)s; }
#CardHint { font-size: 11px; color: %(text_dim)s; }
#StatValue { font-size: 20px; font-weight: 700; color: %(accent)s; }
#StatLabel { font-size: 11px; color: %(text_sub)s; }
#SectionTitle { font-size: 12px; color: %(text_sub)s; font-weight: 600; letter-spacing: 1px; }
#FieldLabel { font-size: 12px; color: %(text_sub)s; }
#ValueLabel { font-size: 12px; color: %(accent)s; font-weight: 600; }

QPushButton {
    background: rgba(255,255,255,0.05);
    border: 1px solid %(border_strong)s;
    border-radius: 10px;
    padding: 7px 14px;
    color: %(text)s;
    font-size: 13px;
}
QPushButton:hover { background: rgba(255,255,255,0.10); border-color: rgba(46,230,197,0.45); }
QPushButton:pressed { background: rgba(46,230,197,0.18); }
QPushButton:disabled { color: %(text_dim)s; border-color: rgba(255,255,255,0.06); }

QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2EE6C5, stop:1 #38BDF8);
    color: #04211C; font-weight: 700; font-size: 14px;
    border: none; border-radius: 12px; padding: 11px 20px;
}
QPushButton#Primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #48F2D4, stop:1 #5FD0FF);
}
QPushButton#Primary:disabled { background: rgba(46,230,197,0.20); color: rgba(4,33,28,0.5); }

QPushButton#Ghost { background: transparent; border: 1px solid %(border_strong)s; }
QPushButton#Ghost:hover { border-color: rgba(46,230,197,0.5); color: %(accent)s; }
QPushButton#Ghost:checked { background: rgba(46,230,197,0.16); border-color: rgba(46,230,197,0.75); color: %(accent)s; }

QPushButton#Danger { background: rgba(248,113,113,0.14); border: 1px solid rgba(248,113,113,0.4); color: #FFB4B4; }
QPushButton#Danger:hover { background: rgba(248,113,113,0.24); }

QToolButton#Help {
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.14);
    border-radius: 9px; color: %(text_sub)s; font-size: 11px; font-weight: 700;
}
QToolButton#Help:hover { border-color: %(accent)s; color: %(accent)s; background: rgba(46,230,197,0.12); }

QLineEdit, QComboBox, QSpinBox {
    background: rgba(255,255,255,0.04);
    border: 1px solid %(border_strong)s;
    border-radius: 9px;
    padding: 6px 10px;
    color: %(text)s;
    selection-background-color: rgba(46,230,197,0.35);
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: %(accent)s; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #101A28; border: 1px solid %(border_strong)s;
    selection-background-color: rgba(46,230,197,0.25);
    color: %(text)s; outline: none;
}
QSpinBox::up-button, QSpinBox::down-button { width: 16px; background: transparent; border: none; }

QSlider::groove:horizontal {
    height: 4px; background: rgba(255,255,255,0.10); border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2EE6C5, stop:1 #38BDF8);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #E8F0FA; border: 2px solid #2EE6C5;
    width: 14px; height: 14px; margin: -7px 0; border-radius: 9px;
}
QSlider::handle:horizontal:hover { background: #FFFFFF; }

QProgressBar {
    background: rgba(255,255,255,0.07);
    border: none; border-radius: 6px; height: 12px; text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2EE6C5, stop:0.5 #38BDF8, stop:1 #A78BFA);
    border-radius: 6px;
}

QListWidget {
    background: transparent; border: none; outline: none;
}
QListWidget::item {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 10px;
    margin: 3px 2px;
    padding: 6px;
}
QListWidget::item:selected {
    background: rgba(46,230,197,0.16);
    border: 1px solid rgba(46,230,197,0.45);
    color: %(text)s;
}
QListWidget::item:hover { background: rgba(255,255,255,0.07); }

QScrollArea { background: transparent; border: none; }
QScrollBar:vertical {
    background: transparent; width: 8px; margin: 4px 2px;
}
QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.16); border-radius: 4px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: rgba(46,230,197,0.5); }
QScrollBar::add-line, QScrollBar::sub-line { height: 0px; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QSplitter::handle { background: rgba(255,255,255,0.06); width: 3px; }

QLabel#DropHint { color: %(text_dim)s; font-size: 12px; }
#PreviewArea {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0A111C, stop:1 #0C1826);
    border: 1px solid %(border)s; border-radius: 14px;
}
#LogView {
    background: rgba(4,8,14,0.6); border: 1px solid rgba(255,255,255,0.05);
    border-radius: 10px; color: #9FB4CE; font-family: "Consolas"; font-size: 11px;
}
QCheckBox { color: %(text)s; font-size: 12px; spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid rgba(255,255,255,0.22); background: rgba(255,255,255,0.04);
}
QCheckBox::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2EE6C5, stop:1 #38BDF8);
    border: 1px solid #2EE6C5;
}
QToolTip {
    background: #0E1826; color: %(text)s; border: 1px solid rgba(46,230,197,0.4);
    border-radius: 8px; padding: 8px; font-size: 12px;
}
QMenu {
    background: #0E1826; border: 1px solid %(border_strong)s; border-radius: 10px; padding: 6px;
}
QMenu::item { padding: 7px 22px; border-radius: 6px; }
QMenu::item:selected { background: rgba(46,230,197,0.18); }
QDialog { background: #0A111C; }
""" % {
    "font": FONT_FAMILY, "text": TEXT, "text_sub": TEXT_SUB, "text_dim": TEXT_DIM,
    "accent": ACCENT, "gold": GOLD, "card": CARD, "border": BORDER,
    "border_strong": BORDER_STRONG,
}
