# -*- coding: utf-8 -*-
"""通用控件：科技风开关、卡片、帮助按钮、拖拽区、图片预览"""
import os
from PyQt5.QtCore import Qt, QRectF, QSize, QVariantAnimation, pyqtSignal, QPoint
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap, QPen, QBrush, QLinearGradient
from PyQt5.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QMessageBox,
                             QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from . import theme


class ToggleSwitch(QAbstractButton):
    """iOS 风格自绘开关，青绿高亮，带滑动动画。"""

    def __init__(self, parent=None, checked=False):
        super(ToggleSwitch, self).__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(46, 24)
        self.setCursor(Qt.PointingHandCursor)
        self._offset = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._start_anim)

    def _start_anim(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _on_anim(self, value):
        self._offset = float(value)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        rect = QRectF(0, 0, w, h)
        if self.isChecked():
            g = QLinearGradient(0, 0, w, 0)
            g.setColorAt(0, QColor(theme.ACCENT))
            g.setColorAt(1, QColor(theme.ACCENT_2))
            p.setBrush(QBrush(g))
            p.setPen(QPen(QColor(46, 230, 197, 160), 1))
        else:
            p.setBrush(QBrush(QColor(255, 255, 255, 18)))
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawRoundedRect(rect, h / 2.0, h / 2.0)
        # 滑块
        d = h - 6
        x = 3 + self._offset * (w - d - 6)
        p.setBrush(QBrush(QColor(255, 255, 255, 240)))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(x, 3, d, d))


class HelpButton(QToolButton):
    """右上角 ? 按钮：点击弹出大白话名词解释。"""

    def __init__(self, title, text, parent=None):
        super(HelpButton, self).__init__(parent)
        self.setObjectName("Help")
        self.setText("?")
        self.setFixedSize(20, 20)
        self.setCursor(Qt.PointingHandCursor)
        self._title = title
        self._text = text
        self.setToolTip(text)
        self.clicked.connect(self._show)

    def _show(self):
        box = QMessageBox(self)
        box.setWindowTitle("名词解析 · %s" % self._title)
        box.setText("<b style='color:#2EE6C5'>%s</b><br><br>%s" % (self._title, self._text.replace("\n", "<br>")))
        box.setIcon(QMessageBox.NoIcon)
        box.setStyleSheet(
            "QMessageBox{background:#0C1622;} QLabel{color:#E8F0FA;font-size:13px;min-width:360px;}"
            "QPushButton{background:rgba(46,230,197,0.15);border:1px solid rgba(46,230,197,0.4);"
            "border-radius:8px;padding:6px 18px;color:#E8F0FA;}"
        )
        box.exec_()


class Card(QFrame):
    """带标题、说明和 ? 帮助按钮的玻璃拟态卡片。"""

    def __init__(self, title, hint="", help_title="", help_text="", parent=None):
        super(Card, self).__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 14)
        self.body.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        tbox = QVBoxLayout()
        tbox.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")
        tbox.addWidget(self.title_label)
        if hint:
            h = QLabel(hint)
            h.setObjectName("CardHint")
            h.setWordWrap(True)
            tbox.addWidget(h)
        head.addLayout(tbox, 1)
        head.addStretch(0)
        if help_text:
            head.addWidget(HelpButton(help_title or title, help_text), 0, Qt.AlignTop)
        self.body.addLayout(head)

    def add(self, widget, stretch=0):
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout):
        self.body.addLayout(layout)
        return layout


class DropArea(QFrame):
    """拖拽导入区：支持拖入图片 / 文件夹，也支持单击选择。"""
    dropped = pyqtSignal(list)
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super(DropArea, self).__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(96)
        self._hover = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(4)
        self.icon_label = QLabel("＋")
        self.icon_label.setAlignment(Qt.AlignCenter)
        f = QFont(theme.FONT_FAMILY, 22, QFont.Bold)
        self.icon_label.setFont(f)
        self.icon_label.setStyleSheet("color:%s;" % theme.ACCENT)
        self.main_label = QLabel("把照片拖进来，或点击选择")
        self.main_label.setAlignment(Qt.AlignCenter)
        self.main_label.setStyleSheet("color:%s;font-size:13px;font-weight:600;" % theme.TEXT)
        self.sub_label = QLabel("支持 JPG / PNG / WEBP / BMP / TIFF，可整文件夹拖入")
        self.sub_label.setAlignment(Qt.AlignCenter)
        self.sub_label.setObjectName("DropHint")
        lay.addWidget(self.icon_label)
        lay.addWidget(self.main_label)
        lay.addWidget(self.sub_label)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._hover:
            p.setBrush(QColor(46, 230, 197, 22))
            p.setPen(QPen(QColor(46, 230, 197, 150), 1.6, Qt.DashLine))
        else:
            p.setBrush(QColor(255, 255, 255, 6))
            p.setPen(QPen(QColor(255, 255, 255, 30), 1.2, Qt.DashLine))
        p.drawRoundedRect(rect, 14, 14)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self._hover = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._hover = False
        self.update()

    def dropEvent(self, e):
        self._hover = False
        self.update()
        paths = []
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p:
                paths.append(p)
        if paths:
            self.dropped.emit(paths)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class ImagePreview(QLabel):
    """等比缩放显示图片，带空状态提示；支持手动框选遮挡区域。"""

    manual_box_added = pyqtSignal(tuple)

    def __init__(self, placeholder="选择左侧照片后，这里会显示预览", parent=None):
        super(ImagePreview, self).__init__(parent)
        self.setObjectName("PreviewArea")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._pix = None
        self._placeholder = placeholder
        self.setText(placeholder)
        self.setStyleSheet("color:%s;font-size:13px;" % theme.TEXT_DIM)
        self._display_rect = QRectF()
        self._overlay = []
        self._select_mode = False
        self._dragging = False
        self._start = None
        self._cur = None
        self.setMouseTracking(True)
        self.setCursor(Qt.ArrowCursor)

    # ------------------------------------------------ 显示
    def set_image_path(self, path):
        if not path or not os.path.exists(path):
            self.clear_image()
            return
        pix = QPixmap(path)
        if pix.isNull():
            self.clear_image("这张图暂时无法预览")
            return
        self._pix = pix
        self.setText("")
        self._render()

    def set_pixmap(self, pix):
        self._pix = pix
        self.setText("")
        self._render()

    def set_pixmap_from_image(self, qimage):
        if qimage is None:
            return
        self.set_pixmap(QPixmap.fromImage(qimage))

    def clear_image(self, text=None):
        self._pix = None
        self._display_rect = QRectF()
        self.setText(text or self._placeholder)

    def _render(self):
        if self._pix is None:
            return
        area = self.contentsRect()
        scaled = self._pix.scaled(max(50, area.width() - 12), max(50, area.height() - 12),
                                  Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(scaled)
        self._display_rect = QRectF(
            area.x() + (area.width() - scaled.width()) / 2.0,
            area.y() + (area.height() - scaled.height()) / 2.0,
            scaled.width(), scaled.height())
        self.update()

    def resizeEvent(self, e):
        super(ImagePreview, self).resizeEvent(e)
        if self._pix is not None:
            self._render()

    # ------------------------------------------------ 框选
    def set_select_mode(self, on):
        self._select_mode = bool(on)
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)

    def set_overlay_boxes(self, boxes):
        self._overlay = list(boxes or [])
        self.update()

    def paintEvent(self, e):
        super(ImagePreview, self).paintEvent(e)
        if self._pix is None or self._display_rect.isEmpty():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for (x1, y1, x2, y2) in self._overlay:
            r = self._proportion_to_rect(x1, y1, x2, y2)
            p.setBrush(QColor(248, 113, 113, 45))
            p.setPen(QPen(QColor(248, 113, 113, 220), 2, Qt.DashLine))
            p.drawRect(r)
        if self._dragging and self._start and self._cur:
            r = QRectF(self._start, self._cur).normalized()
            p.setBrush(QColor(46, 230, 197, 45))
            p.setPen(QPen(QColor(46, 230, 197, 220), 2, Qt.DashLine))
            p.drawRect(r)

    def _proportion_to_rect(self, x1, y1, x2, y2):
        d = self._display_rect
        return QRectF(d.x() + x1 * d.width(), d.y() + y1 * d.height(),
                      (x2 - x1) * d.width(), (y2 - y1) * d.height())

    def _to_proportion(self, pos):
        d = self._display_rect
        if d.isEmpty():
            return 0.0, 0.0
        x = (pos.x() - d.x()) / d.width()
        y = (pos.y() - d.y()) / d.height()
        return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))

    def mousePressEvent(self, e):
        if self._select_mode and self._pix is not None and e.button() == Qt.LeftButton:
            self._dragging = True
            self._start = e.pos()
            self._cur = e.pos()
            self.update()
            return
        super(ImagePreview, self).mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._cur = e.pos()
            self.update()
            return
        super(ImagePreview, self).mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._dragging = False
            x1, y1 = self._to_proportion(self._start)
            x2, y2 = self._to_proportion(e.pos())
            self._start = self._cur = None
            self.update()
            if abs(x2 - x1) > 0.02 and abs(y2 - y1) > 0.02:
                box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
                self.manual_box_added.emit(box)
            return
        super(ImagePreview, self).mouseReleaseEvent(e)


class StatBlock(QWidget):
    """统计小卡：数值 + 说明。"""

    def __init__(self, label, value="0", color=None, parent=None):
        super(StatBlock, self).__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.value_label = QLabel(str(value))
        self.value_label.setObjectName("StatValue")
        if color:
            self.value_label.setStyleSheet("color:%s;font-size:20px;font-weight:700;" % color)
        self.text_label = QLabel(label)
        self.text_label.setObjectName("StatLabel")
        lay.addWidget(self.value_label)
        lay.addWidget(self.text_label)

    def set_value(self, v):
        self.value_label.setText(str(v))


def make_button(text, primary=False, ghost=False, danger=False):
    btn = QPushButton(text)
    if primary:
        btn.setObjectName("Primary")
    elif danger:
        btn.setObjectName("Danger")
    elif ghost:
        btn.setObjectName("Ghost")
    btn.setCursor(Qt.PointingHandCursor)
    return btn


def hline():
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background:rgba(255,255,255,0.07);border:none;")
    return line
