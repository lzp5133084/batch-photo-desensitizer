# -*- coding: utf-8 -*-
"""
批量相片去敏工具 · 主程序（PyQt5）
====================================
作者：军哥懂保   微信：xunijiayuan
功能：批量清除照片元数据 / 人脸打码 / 车牌遮挡 / 证件号打码，全程本地离线。
"""
import argparse
import os
import sys
import time

from PIL import Image, ImageOps
from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal, QSize
from PyQt5.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap, QLinearGradient, QBrush
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
                             QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
                             QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider,
                             QSplitter, QVBoxLayout, QWidget)

from . import APP_NAME, APP_VERSION, AUTHOR, WECHAT, theme
from . import api
from .engine import (Options, SUPPORTED_EXT, list_images, process_batch, process_file,
                     read_metadata_summary, desensitize, ocr_status, ocr_available)
from .ocr_local import download_models, models_ready
from .store import (APP_ROOT, add_history, dir_size_mb, latest_mtime, load_config,
                    load_history, save_config)
from .widgets import Card, DropArea, HelpButton, ImagePreview, StatBlock, ToggleSwitch, hline, make_button


# -------------------------------------------------------------------------------
# 后台线程
# -------------------------------------------------------------------------------
class BatchWorker(QThread):
    progress = pyqtSignal(int, int, object)
    finished_summary = pyqtSignal(dict)
    log_msg = pyqtSignal(str)

    def __init__(self, paths, options, parent=None):
        super(BatchWorker, self).__init__(parent)
        self.paths = list(paths)
        self.options = options
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        summary = process_batch(
            self.paths, self.options,
            progress=lambda done, total, res: self.progress.emit(done, total, res),
            should_stop=lambda: self._stop,
            log=lambda s: self.log_msg.emit(s),
        )
        self.finished_summary.emit(summary)


class PreviewWorker(QThread):
    done = pyqtSignal(object, object, dict, str)

    def __init__(self, path, options, parent=None):
        super(PreviewWorker, self).__init__(parent)
        self.path = path
        self.options = options

    @staticmethod
    def _to_qimage(pil_img):
        rgb = pil_img.convert("RGB")
        w, h = rgb.size
        data = rgb.tobytes("raw", "RGB")
        img = QImage(data, w, h, w * 3, QImage.Format_RGB888)
        return img.copy()

    def run(self):
        try:
            im = Image.open(self.path)
            im.load()
            before_pil = im.copy()
            if self.options.keep_orientation:
                try:
                    before_pil = ImageOps.exif_transpose(before_pil)
                except Exception:
                    pass
            mdeg = 0
            try:
                mdeg = int((getattr(self.options, "rotate_map", {}) or {}).get(os.path.abspath(self.path), 0) or 0)
            except Exception:
                mdeg = 0
            after, stats = desensitize(im.copy(), self.options, manual_deg=mdeg)
            deg = stats.get("rotated", 0)
            if deg:
                before_pil = before_pil.rotate(deg, expand=True, resample=Image.BICUBIC)
            self.done.emit(self._to_qimage(before_pil), self._to_qimage(after), stats, "")
        except Exception as e:
            self.done.emit(None, None, {}, "%s: %s" % (type(e).__name__, e))


# -------------------------------------------------------------------------------
# 主窗口
# -------------------------------------------------------------------------------
class MainWindow(QMainWindow):

    def __init__(self):
        super(MainWindow, self).__init__()
        self.paths = []
        self.results = {}
        self.manual_boxes = []
        self.rotate_map = {}
        self.cfg = load_config()
        self.worker = None
        self.preview_worker = None
        self.current_preview_path = None
        self._current_out_path = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self.persist_config)

        self.setWindowTitle("%s · %s" % (APP_NAME, APP_VERSION))
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(1520, 950)
        self.setMinimumSize(1240, 800)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())
        outer.addWidget(self._build_body(), 1)
        outer.addWidget(self._build_footer())

        self.restore_config()
        self.preview.manual_box_added.connect(self.on_manual_box)
        self._refresh_app_info()
        self.refresh_ocr_label()
        self._append_log("程序已启动 · %s v%s · 全程本地离线处理" % (APP_NAME, APP_VERSION))
        self._append_log("作者 %s（微信 %s），照片不会上传到任何服务器。" % (AUTHOR, WECHAT))

    # ------------------------------------------------------------------ 顶部
    def _build_header(self):
        bar = QFrame()
        bar.setObjectName("Header")
        bar.setFixedHeight(92)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(26, 12, 26, 12)
        lay.setSpacing(14)

        logo = QLabel()
        logo.setFixedSize(52, 52)
        logo.setStyleSheet("background:transparent;")
        logo.setPixmap(self._make_logo(52))
        lay.addWidget(logo)

        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        t = QLabel("%s" % APP_NAME)
        t.setObjectName("AppTitle")
        row1.addWidget(t)
        v = QLabel("v%s" % APP_VERSION)
        v.setObjectName("VersionBadge")
        row1.addWidget(v, 0, Qt.AlignVCenter)
        safe = QLabel("100% 本地离线 · 照片不出电脑")
        safe.setObjectName("SafeBadge")
        row1.addWidget(safe, 0, Qt.AlignVCenter)
        row1.addStretch(1)
        title_box.addLayout(row1)
        sub = QLabel("一键抹掉照片里的定位、设备型号、人脸、车牌与证件号，交付出去更安心")
        sub.setObjectName("AppSub")
        title_box.addWidget(sub)
        lay.addLayout(title_box, 1)

        info_box = QVBoxLayout()
        info_box.setSpacing(2)
        info_box.setAlignment(Qt.AlignRight)
        self.size_label = QLabel("程序体积 —")
        self.size_label.setObjectName("AuthorInfo")
        self.size_label.setAlignment(Qt.AlignRight)
        info_box.addWidget(self.size_label)
        self.time_label = QLabel("最后更新 —")
        self.time_label.setObjectName("AuthorInfo")
        self.time_label.setAlignment(Qt.AlignRight)
        info_box.addWidget(self.time_label)
        author = QLabel("作者 军哥懂保 · 微信 xunijiayuan")
        author.setObjectName("AuthorName")
        author.setAlignment(Qt.AlignRight)
        info_box.addWidget(author)
        lay.addLayout(info_box)
        return bar

    def _make_logo(self, size):
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, size, size)
        g.setColorAt(0, QColor(46, 230, 197))
        g.setColorAt(1, QColor(99, 102, 241))
        p.setBrush(QBrush(g))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, size, size, 15, 15)
        p.setPen(QColor(6, 22, 20))
        f = QFont("Microsoft YaHei", int(size * 0.36), QFont.Bold)
        p.setFont(f)
        p.drawText(pix.rect(), Qt.AlignCenter, "敏")
        p.end()
        return pix

    # ------------------------------------------------------------------ 主体
    def _build_body(self):
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(6)
        split.setChildrenCollapsible(False)

        # 左：导入 + 队列
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(18, 16, 8, 10)
        lv.setSpacing(12)

        card_import = Card("导入照片", "支持单张、多张、整个文件夹批量拖入",
                           "导入方式",
                           "点击「添加照片」可一次选多张；「添加文件夹」会把文件夹里的照片全部加进来（包含子文件夹）。\n"
                           "也可以直接把照片或文件夹拖到虚线框里。程序只读取，不会修改原图。\n"
                           "「清空」只会把队列清掉，不会删除你电脑上的照片。")
        self.drop = DropArea()
        self.drop.dropped.connect(self.add_paths)
        self.drop.clicked.connect(self.pick_files)
        card_import.add(self.drop)
        row = QHBoxLayout()
        row.setSpacing(8)
        b1 = make_button("添加照片")
        b1.clicked.connect(self.pick_files)
        b2 = make_button("添加文件夹")
        b2.clicked.connect(self.pick_folder)
        b3 = make_button("清空", ghost=True)
        b3.clicked.connect(self.clear_queue)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addWidget(b3)
        card_import.add_layout(row)
        lv.addWidget(card_import)

        card_queue = Card("待处理队列", "勾选状态会在处理时实时更新",
                          "队列管理",
                          "点队列里的任意一张照片，右侧预览区就会显示它；再点「试算这张」可以只看这一张的去敏效果，"
                          "不会写文件。\n"
                          "○ 表示还没处理，✓ 表示已处理成功，✕ 表示失败（日志里会写明原因）。\n"
                          "照片按导入顺序处理，输出文件名从 IMG_001 开始编号。")
        self.list_widget = QListWidget()
        self.list_widget.setIconSize(QSize(26, 26))
        self.list_widget.setSpacing(1)
        self.list_widget.itemClicked.connect(self.on_item_clicked)
        self.list_widget.setMinimumHeight(220)
        card_queue.add(self.list_widget, 1)

        stats = QHBoxLayout()
        stats.setSpacing(18)
        self.stat_total = StatBlock("已选照片")
        self.stat_done = StatBlock("已处理", color=theme.OK)
        self.stat_fail = StatBlock("失败", color=theme.DANGER)
        stats.addWidget(self.stat_total)
        stats.addWidget(self.stat_done)
        stats.addWidget(self.stat_fail)
        stats.addStretch(1)
        card_queue.add_layout(stats)
        lv.addWidget(card_queue, 1)
        split.addWidget(left)

        # 中：预览
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(8, 16, 8, 10)
        mv.setSpacing(10)

        card_prev = Card("效果预览", "先试算一张，确认满意再批量处理",
                         "试算、框选与方向纠正",
                         "「试算这张」只在内存里处理，不产生文件，几秒钟就能看到效果。\n"
                         "「原图 / 去敏后」按钮用来对比前后差别。\n"
                         "「框选遮挡」：点开后，在图片上按住鼠标拖一个框，就能手动遮住水印、门牌、"
                         "固定位置的证件——这个框会对队列里所有照片的相同位置生效，适合模板化拍摄。\n"
                         "「左转 90° / 右转 90° / 翻转 180° / 复位」：只对当前这张照片生效。"
                         "如果自动判断的方向不对（尤其是微信转发、修图导出这类没有方向标记的照片），"
                         "点一下就能摆正；设置会按文件名记住，批量处理时逐张应用。")
        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        self.btn_tab_before = make_button("原图")
        self.btn_tab_after = make_button("去敏后", primary=True)
        self.btn_tab_before.setCheckable(True)
        self.btn_tab_after.setCheckable(True)
        self.btn_tab_before.setChecked(False)
        self.btn_tab_after.setChecked(True)
        self.btn_tab_before.clicked.connect(lambda: self.switch_preview_tab("before"))
        self.btn_tab_after.clicked.connect(lambda: self.switch_preview_tab("after"))
        tabs.addWidget(self.btn_tab_before)
        tabs.addWidget(self.btn_tab_after)
        tabs.addStretch(1)
        self.btn_preview = make_button("试算这张")
        self.btn_preview.clicked.connect(self.run_preview)
        tabs.addWidget(self.btn_preview)
        self.btn_manual = make_button("框选遮挡", ghost=True)
        self.btn_manual.setCheckable(True)
        self.btn_manual.clicked.connect(self.toggle_manual_mode)
        tabs.addWidget(self.btn_manual)
        self.btn_clear_manual = make_button("清除框选", ghost=True)
        self.btn_clear_manual.clicked.connect(self.clear_manual_boxes)
        tabs.addWidget(self.btn_clear_manual)
        card_prev.add_layout(tabs)

        # 手动方向纠正：逐张生效，适合 EXIF 缺失、自动判断没把握的照片
        rot_bar = QHBoxLayout()
        rot_bar.setSpacing(6)
        rot_lab = QLabel("这张的方向：")
        rot_lab.setObjectName("FieldLabel")
        rot_bar.addWidget(rot_lab)
        self.lbl_rotate = QLabel("0°")
        self.lbl_rotate.setObjectName("CardHint")
        self.lbl_rotate.setMinimumWidth(46)
        rot_bar.addWidget(self.lbl_rotate)
        self.btn_rot_l = make_button("左转 90°", ghost=True)
        self.btn_rot_l.clicked.connect(lambda: self.rotate_current(90))
        rot_bar.addWidget(self.btn_rot_l)
        self.btn_rot_r = make_button("右转 90°", ghost=True)
        self.btn_rot_r.clicked.connect(lambda: self.rotate_current(-90))
        rot_bar.addWidget(self.btn_rot_r)
        self.btn_rot_180 = make_button("翻转 180°", ghost=True)
        self.btn_rot_180.clicked.connect(lambda: self.rotate_current(180))
        rot_bar.addWidget(self.btn_rot_180)
        self.btn_rot_reset = make_button("复位", ghost=True)
        self.btn_rot_reset.clicked.connect(lambda: self.rotate_current(0))
        rot_bar.addWidget(self.btn_rot_reset)
        rot_bar.addStretch(1)
        card_prev.add_layout(rot_bar)

        self.preview = ImagePreview()
        card_prev.add(self.preview, 1)

        self.preview_info = QLabel("选中左侧任意照片，可先试算预览效果")
        self.preview_info.setObjectName("CardHint")
        self.preview_info.setWordWrap(True)
        card_prev.add(self.preview_info)
        mv.addWidget(card_prev, 1)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(600)
        self.log_view.setFixedHeight(118)
        self.log_view.setPlainText("")
        mv.addWidget(self.log_view)
        split.addWidget(mid)

        # 右：参数
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 16, 18, 10)
        rv.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 6, 0)
        pv.setSpacing(12)
        pv.addWidget(self._card_meta())
        pv.addWidget(self._card_face())
        pv.addWidget(self._card_plate())
        pv.addWidget(self._card_text())
        pv.addWidget(self._card_output())
        pv.addWidget(self._card_history())
        pv.addWidget(self._card_about())
        pv.addStretch(1)
        scroll.setWidget(panel)
        rv.addWidget(scroll)
        split.addWidget(right)

        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([380, 700, 450])
        self.settings_panel = panel
        return split

    # ------------------------------------------------------------------ 参数卡片
    def _card_meta(self):
        card = Card("元数据清除与方向摆正", "删掉定位、设备、时间等隐藏信息，并自动摆正横竖方向",
                    "元数据（EXIF）与照片方向",
                    "照片里藏着一张「隐形小纸条」：在哪拍的（GPS 经纬度）、几点拍的、用什么手机/相机、"
                    "用什么软件修过图、甚至内置缩略图。\n"
                    "把这些删干净，别人拿到照片就查不出你的位置、行程和设备。\n"
                    "另外这张小纸条里还有一个「方向标记」（Orientation）：手机竖着拍、"
                    "系统却把像素横着存，靠这个标记让相册转过来显示。\n"
                    "本程序的处理顺序是：先按这个标记把照片摆正，再做打码，这样框不会画偏；"
                    "如果照片根本没有方向标记（微信转发、修图软件导出常见），"
                    "程序会用「能不能检出人脸、能不能读出文字」来判断该不该转 90° / 180°，"
                    "判断没把握就不转，绝不乱转。最后重新写文件，不写入任何 EXIF。")
        self.sw_meta = ToggleSwitch(checked=True)
        row = QHBoxLayout()
        lab = QLabel("清除全部元数据（含 GPS 定位）")
        lab.setObjectName("FieldLabel")
        row.addWidget(lab)
        row.addStretch(1)
        row.addWidget(self.sw_meta)
        card.add_layout(row)

        self.cb_icc = QCheckBox("保留色彩配置（ICC，只影响颜色观感，不含隐私）")
        self.cb_icc.setChecked(True)
        card.add(self.cb_icc)

        self.sw_orient = ToggleSwitch(checked=True)
        row2 = QHBoxLayout()
        lab2 = QLabel("自动纠正横竖方向（90° / 180°）")
        lab2.setObjectName("FieldLabel")
        row2.addWidget(lab2, 1)
        row2.addWidget(self.sw_orient)
        card.add_layout(row2)

        self.meta_detail = QLabel("选中照片后可查看它当前包含哪些信息")
        self.meta_detail.setObjectName("CardHint")
        self.meta_detail.setWordWrap(True)
        card.add(self.meta_detail)
        return card

    def _card_face(self):
        card = Card("人脸自动打码", "本地 AI 找出人脸，糊掉五官保护隐私",
                    "人脸检测",
                    "程序用 OpenCV 自带的轻量模型，在本地找出画面里的「人脸框」，"
                    "然后把这块区域打成马赛克/模糊。\n"
                    "它只是找「这是不是一张脸」，不会识别这个人是谁，也不会联网。\n"
                    "灵敏度越高越容易检出（远处小脸、侧脸），但可能多打一些无关区域。")
        self.sw_face = ToggleSwitch(checked=True)
        row = QHBoxLayout()
        lab = QLabel("启用人脸打码")
        lab.setObjectName("FieldLabel")
        row.addWidget(lab)
        row.addStretch(1)
        row.addWidget(self.sw_face)
        card.add_layout(row)

        self.combo_face_mode = QComboBox()
        self.combo_face_mode.addItems(["马赛克（最常用）", "高斯模糊（自然）", "纯色遮盖（最彻底）"])
        self.combo_face_mode.setFixedWidth(190)
        card.add_layout(self._row("打码方式", self.combo_face_mode))

        self.sl_face_strength = self._slider(card, "打码强度", 10, 100, 55)
        self.sl_face_sens = self._slider(card, "检测灵敏度", 20, 95, 55)
        return card

    def _card_plate(self):
        card = Card("车牌自动遮挡", "自动找蓝牌/黄牌/绿牌并遮挡",
                    "车牌识别",
                    "先在画面里按车牌颜色（蓝牌、黄牌、新能源绿牌）挑出长条区域，"
                    "再用本地文字识别（OCR）读一遍，确认上面确实是车牌号码，才打码。\n"
                    "中文车牌反光、倾斜、遮挡时会漏检，建议开启后用预览确认，必要时用「框选遮挡」手动补一块。")
        self.sw_plate = ToggleSwitch(checked=False)
        row = QHBoxLayout()
        lab = QLabel("启用车牌遮挡")
        lab.setObjectName("FieldLabel")
        row.addWidget(lab)
        row.addStretch(1)
        row.addWidget(self.sw_plate)
        card.add_layout(row)

        self.combo_plate_mode = QComboBox()
        self.combo_plate_mode.addItems(["马赛克（最常用）", "高斯模糊（自然）", "纯色遮盖（最彻底）"])
        self.combo_plate_mode.setFixedWidth(190)
        card.add_layout(self._row("打码方式", self.combo_plate_mode))

        self.sl_plate_strength = self._slider(card, "打码强度", 10, 100, 65)
        self.sl_plate_sens = self._slider(card, "检测灵敏度", 20, 95, 50)
        return card

    def _card_text(self):
        card = Card("文字敏感信息", "身份证号、手机号、银行卡号、姓名、地址、邮箱自动打码",
                    "OCR 与敏感文字",
                    "OCR 就是「图片认字」：把照片里的文字读成文本，全程在本机用轻量模型完成。\n"
                    "读到文本后，程序用规则 + 上下文触发关键词匹配敏感内容：\n"
                    "· 身份证：18 位，含出生日期\n· 手机号：11 位，以 1 开头\n· 银行卡：16~19 位连续数字\n"
                    "· 邮箱：a@b.com 格式\n· 姓名：在「姓名/联系人/收件人」等字段后，用姓氏表验证\n"
                    "· 地址：含「省/市/区/街道/路/号」等行政区划或门牌号结构\n"
                    "默认只打这些敏感内容，不影响照片上的其他普通文字。")
        self.sw_text = ToggleSwitch(checked=True)
        row = QHBoxLayout()
        lab = QLabel("启用文字打码")
        lab.setObjectName("FieldLabel")
        row.addWidget(lab)
        row.addStretch(1)
        row.addWidget(self.sw_text)
        card.add_layout(row)

        self.combo_text_mode = QComboBox()
        self.combo_text_mode.addItems(["马赛克（最常用）", "高斯模糊（自然）", "纯色遮盖（最彻底）"])
        self.combo_text_mode.setFixedWidth(190)
        card.add_layout(self._row("打码方式", self.combo_text_mode))

        self.cb_text_all = QCheckBox("所有文字块都打码（识别不出号码时更保险）")
        self.cb_text_all.setChecked(False)
        card.add(self.cb_text_all)

        self.ocr_label = QLabel("本地 OCR 状态检测中…")
        self.ocr_label.setObjectName("CardHint")
        self.ocr_label.setWordWrap(True)
        card.add(self.ocr_label)

        self.btn_ocr = make_button("检查 / 下载 OCR 模型", ghost=True)
        self.btn_ocr.clicked.connect(self.on_download_ocr)
        card.add(self.btn_ocr)
        return card

    def _card_output(self):
        card = Card("输出与命名", "处理后另存新文件，绝不动你的原图",
                    "重命名",
                    "手机相机拍的照片常叫 IMG_20261001_123456.jpg，文件名本身就暴露了拍摄日期；"
                    "微信保存的图片还可能带人名。\n"
                    "开启重命名后会统一改成 IMG_001.jpg 这样的序号名，避免文件名泄露信息。")
        self.sw_rename = ToggleSwitch(checked=True)
        row = QHBoxLayout()
        lab = QLabel("重命名文件（去掉原文件名里的隐私）")
        lab.setObjectName("FieldLabel")
        row.addWidget(lab)
        row.addStretch(1)
        row.addWidget(self.sw_rename)
        card.add_layout(row)

        self.edit_pattern = QLineEdit("IMG_{index:03d}")
        self.edit_pattern.setFixedWidth(190)
        card.add_layout(self._row("命名模板", self.edit_pattern))

        self.cb_random = QCheckBox("使用 14 位随机文件名（更彻底）")
        card.add(self.cb_random)

        self.combo_format = QComboBox()
        self.combo_format.addItems(["保持原格式", "统一转成 JPG", "统一转成 PNG", "统一转成 WEBP"])
        self.combo_format.setFixedWidth(190)
        card.add_layout(self._row("输出格式", self.combo_format))

        self.combo_side = QComboBox()
        self.combo_side.addItems(["不限制尺寸", "最长边 2560px", "最长边 1920px", "最长边 1280px", "最长边 800px"])
        self.combo_side.setFixedWidth(190)
        card.add_layout(self._row("最长边", self.combo_side))

        self.sl_quality = self._slider(card, "JPG 画质", 60, 100, 92)

        out_row = QHBoxLayout()
        out_row.setSpacing(6)
        self.edit_out = QLineEdit()
        self.edit_out.setPlaceholderText("留空 = 每张照片同级的「去敏输出」文件夹")
        out_row.addWidget(self.edit_out, 1)
        b = make_button("选择", ghost=True)
        b.clicked.connect(self.pick_output_dir)
        out_row.addWidget(b)
        card.add_layout(out_row)
        return card

    def pick_output_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹", self.edit_out.text() or self.cfg.get("last_dir", ""))
        if folder:
            self.edit_out.setText(folder)
            self._save_timer.start()

    def _card_history(self):
        card = Card("历史任务", "参数会记住，一键套用之前的设置", "操作记忆",
                    "每次批量处理完成后，程序都会记下：处理时间、多少张、失败几张、用了哪些参数。\n"
                    "下次点「套用」就能恢复当时的全部设置，不用重新调。")
        self.history_box = QVBoxLayout()
        self.history_box.setSpacing(6)
        card.add_layout(self.history_box)
        self.refresh_history()
        return card

    def _card_about(self):
        card = Card("关于本程序", "", "离线安全",
                    "本程序不需要联网、不需要登录，也不会把照片上传到任何服务器。\n"
                    "唯一会联网的情况：你主动点击「下载 OCR 模型」，从国内镜像下载约 15MB 的识别模型。\n"
                    "处理结果默认另存到新文件夹，原图保持原样。")
        self.label_about = QLabel("")
        self.label_about.setWordWrap(True)
        self.label_about.setStyleSheet("color:%s;font-size:12px;line-height:150%%;" % theme.TEXT_SUB)
        card.add(self.label_about)

        self.cb_api = QCheckBox("开放本地接口（端口 8765，供其他程序调用）")
        self.cb_api.stateChanged.connect(self.on_api_toggle)
        card.add(self.cb_api)
        return card

    # ------------------------------------------------------------------ 小工具
    def _row(self, label, widget):
        row = QHBoxLayout()
        row.setSpacing(8)
        lab = QLabel(label)
        lab.setObjectName("FieldLabel")
        row.addWidget(lab)
        row.addStretch(1)
        row.addWidget(widget)
        return row

    def _slider(self, card, label, mn, mx, value):
        row = QHBoxLayout()
        row.setSpacing(8)
        lab = QLabel(label)
        lab.setObjectName("FieldLabel")
        row.addWidget(lab)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(mn, mx)
        slider.setValue(value)
        slider.setFixedHeight(20)
        val = QLabel(str(value))
        val.setObjectName("ValueLabel")
        val.setFixedWidth(30)
        val.setAlignment(Qt.AlignRight)
        slider.valueChanged.connect(lambda v, l=val: l.setText(str(v)))
        slider.valueChanged.connect(lambda _v: self._save_timer.start())
        row.addWidget(slider, 1)
        row.addWidget(val)
        card.add_layout(row)
        return slider

    # ------------------------------------------------------------------ 底部
    def _build_footer(self):
        bar = QFrame()
        bar.setObjectName("Header")
        bar.setFixedHeight(84)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(26, 12, 26, 12)
        lay.setSpacing(16)

        info = QVBoxLayout()
        info.setSpacing(6)
        self.status_label = QLabel("就绪 · 等待导入照片")
        self.status_label.setStyleSheet("color:%s;font-size:12px;" % theme.TEXT_SUB)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(10)
        info.addWidget(self.status_label)
        info.addWidget(self.progress)
        lay.addLayout(info, 1)

        self.btn_start = make_button("开始去敏", primary=True)
        self.btn_start.setMinimumWidth(160)
        self.btn_start.clicked.connect(self.start_batch)
        lay.addWidget(self.btn_start)

        self.btn_stop = make_button("停止", danger=True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_batch)
        lay.addWidget(self.btn_stop)

        self.btn_open = make_button("打开输出目录", ghost=True)
        self.btn_open.clicked.connect(self.open_output_dir)
        lay.addWidget(self.btn_open)
        return bar

    # ------------------------------------------------------------------ 文件导入
    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择照片", self.cfg.get("last_dir", ""),
            "图片文件 (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff);;所有文件 (*)")
        if files:
            self.cfg["last_dir"] = os.path.dirname(files[0])
            self._save_timer.start()
            self.add_paths(files)

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择照片文件夹", self.cfg.get("last_dir", ""))
        if folder:
            self.cfg["last_dir"] = folder
            self._save_timer.start()
            self.add_paths([folder])

    def add_paths(self, paths):
        files = []
        for p in paths:
            if os.path.isdir(p):
                files.extend(list_images(p))
            elif p.lower().endswith(SUPPORTED_EXT):
                files.append(p)
        added = 0
        existing = set(self.paths)
        for f in files:
            if f not in existing:
                self.paths.append(f)
                existing.add(f)
                self._append_item(f)
                added += 1
        self.stat_total.set_value(len(self.paths))
        if added:
            self._append_log("已导入 %d 张照片（共 %d 张待处理）" % (added, len(self.paths)))
            self.status_label.setText("就绪 · 共 %d 张照片等待处理" % len(self.paths))
        elif files:
            self._append_log("这些照片已经在队列里了")

    def _append_item(self, path):
        item = QListWidgetItem()
        item.setText(self._item_text(path, "待处理"))
        item.setToolTip(path)
        item.setData(Qt.UserRole, path)
        item.setIcon(self._type_icon(path))
        self.list_widget.addItem(item)

    @staticmethod
    def _item_text(path, state):
        try:
            size = os.path.getsize(path) / 1024.0 / 1024.0
            size_txt = "%.1f MB" % size if size >= 1 else "%d KB" % max(1, int(size * 1024))
        except Exception:
            size_txt = "—"
        name = os.path.basename(path)
        if len(name) > 26:
            name = name[:12] + "…" + name[-10:]
        mark = {"待处理": "○", "完成": "✓", "失败": "✕"}.get(state, "○")
        return "%s  %s    %s   %s" % (mark, name, size_txt, state)

    def _type_icon(self, path):
        ext = os.path.splitext(path)[1].lower().replace(".", "").upper() or "IMG"
        text = ext[:3]
        pix = QPixmap(52, 52)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(46, 230, 197) if ext in ("JPG", "JPEG") else QColor(96, 165, 250) if ext == "PNG" else QColor(167, 139, 250)
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 30))
        p.setPen(QColor(color.red(), color.green(), color.blue(), 130))
        p.drawRoundedRect(2, 2, 48, 48, 12, 12)
        p.setPen(color)
        p.setFont(QFont("Microsoft YaHei", 8, QFont.Bold))
        p.drawText(pix.rect(), Qt.AlignCenter, text)
        p.end()
        return QIcon(pix)

    def clear_queue(self):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "正在处理", "请先停止当前任务，再清空队列。")
            return
        self.paths = []
        self.results = {}
        self.rotate_map.clear()
        self.list_widget.clear()
        self.stat_total.set_value(0)
        self.stat_done.set_value(0)
        self.stat_fail.set_value(0)
        self.preview.clear_image()
        self.preview_info.setText("选中左侧任意照片，可先试算预览效果")
        self.status_label.setText("就绪 · 等待导入照片")
        self.progress.setValue(0)
        self._append_log("队列已清空")

    def on_item_clicked(self, item):
        path = item.data(Qt.UserRole)
        if not path:
            return
        self.current_preview_path = path
        self.preview.set_image_path(path)
        self.switch_preview_tab("before", silent=True)
        info = read_metadata_summary(path)
        parts = ["文件名：%s" % os.path.basename(path)]
        if info.get("width"):
            parts.append("尺寸 %dx%d" % (info["width"], info["height"]))
        ori = int(info.get("orientation", 1) or 1)
        if ori in (2, 3, 4, 5, 6, 7, 8):
            parts.append("EXIF 方向 %d（会自动摆正）" % ori)
        elif info.get("has_exif"):
            parts.append("无方向标记（按内容判断）")
        else:
            parts.append("无 EXIF（按内容判断方向）")
        if info.get("tags"):
            parts.append("元数据 %d 项" % info["tags"])
            if info.get("camera"):
                parts.append("设备：%s" % info["camera"])
            if info.get("time"):
                parts.append("拍摄时间：%s" % info["time"])
            if info.get("has_gps"):
                parts.append("含 GPS 定位！")
        else:
            parts.append("未发现元数据")
        self.meta_detail.setText(" · ".join(parts))
        self._update_rotate_label()
        self.preview_info.setText("已选中：%s　点击「试算这张」查看去敏效果" % os.path.basename(path))

    def _sync_tab_style(self):
        for btn, active in ((self.btn_tab_before, self.btn_tab_before.isChecked()),
                            (self.btn_tab_after, self.btn_tab_after.isChecked())):
            name = "Primary" if active else "Ghost"
            if btn.objectName() != name:
                btn.setObjectName(name)
                btn.style().unpolish(btn)
                btn.style().polish(btn)

    # ------------------------------------------------------------------ 预览
    def switch_preview_tab(self, which, silent=False):
        self.btn_tab_before.setChecked(which == "before")
        self.btn_tab_after.setChecked(which == "after")
        self._sync_tab_style()
        if silent:
            return
        if not self.current_preview_path:
            return
        if which == "before":
            self.preview.set_image_path(self.current_preview_path)
        else:
            if self._current_out_path and os.path.exists(self._current_out_path):
                self.preview.set_image_path(self._current_out_path)
            else:
                self.run_preview()

    def run_preview(self):
        if not self.current_preview_path:
            QMessageBox.information(self, "还没选照片", "请先在左侧队列里点击一张照片。")
            return
        if self.preview_worker is not None and self.preview_worker.isRunning():
            return
        options = self.collect_options()
        self.preview_info.setText("正在试算…（人脸/车牌/文字检测需要几秒钟）")
        self.btn_preview.setEnabled(False)
        self.preview_worker = PreviewWorker(self.current_preview_path, options, self)
        self.preview_worker.done.connect(self.on_preview_done)
        self.preview_worker.start()

    def on_preview_done(self, before, after, stats, error):
        self.btn_preview.setEnabled(True)
        if error:
            self.preview_info.setText("试算失败：%s" % error)
            self._append_log("试算失败：%s" % error)
            return
        self._current_out_path = None
        self.btn_tab_before.setChecked(False)
        self.btn_tab_after.setChecked(True)
        self._sync_tab_style()
        self.preview.set_pixmap_from_image(after)
        rot = stats.get("rotated", 0)
        msg = ("试算结果：人脸 %d 处 · 车牌 %d 处 · 文字 %d 处（原图尺寸 %dx%d）"
               % (stats.get("faces", 0), stats.get("plates", 0), stats.get("texts", 0),
                  after.width(), after.height()))
        if rot:
            msg += "　方向已旋转 %d° 摆正" % rot
        self.preview_info.setText(msg)
        self._append_log("试算完成：人脸%d 车牌%d 文字%d%s"
                         % (stats.get("faces", 0), stats.get("plates", 0), stats.get("texts", 0),
                            (" 方向纠正%d°" % rot) if rot else ""))

    def toggle_manual_mode(self, checked):
        self.preview.set_select_mode(bool(checked))
        if checked:
            self.preview_info.setText("框选模式已开启：在图片上按住拖动，即可画出一块永久遮挡区域（对所有照片生效）")
        else:
            self.preview_info.setText("框选模式已关闭")

    def on_manual_box(self, box):
        self.manual_boxes.append(box)
        self.preview.set_overlay_boxes(self.manual_boxes)
        self.btn_manual.setChecked(True)
        self.preview_info.setText("已添加 %d 个手动遮挡区域（批量处理时全部生效）" % len(self.manual_boxes))

    def clear_manual_boxes(self):
        self.manual_boxes = []
        self.preview.set_overlay_boxes([])
        self.btn_manual.setChecked(False)
        self.preview.set_select_mode(False)
        self.preview_info.setText("已清除所有手动遮挡区域")

    def _update_rotate_label(self):
        if not self.current_preview_path:
            self.lbl_rotate.setText("0°")
            return
        deg = int(self.rotate_map.get(os.path.abspath(self.current_preview_path), 0) or 0)
        self.lbl_rotate.setText("%d°" % deg)

    def rotate_current(self, delta):
        """手动旋转当前照片：delta=90 左转 / -90 右转 / 180 翻转 / 0 复位。
        角度按文件路径记录，批量处理时逐张生效。
        """
        path = self.current_preview_path
        if not path:
            QMessageBox.information(self, "提示", "请先选中一张照片。")
            return
        key = os.path.abspath(path)
        if delta == 0:
            self.rotate_map.pop(key, None)
        else:
            self.rotate_map[key] = (int(self.rotate_map.get(key, 0)) + delta) % 360
        self._update_rotate_label()
        deg = int(self.rotate_map.get(key, 0) or 0)
        self._append_log("手动旋转：%s → %d°" % (os.path.basename(path), deg))
        self.preview_info.setText("已设置方向 %d°，正在重新试算…" % deg)
        self.switch_preview_tab("after")
        self.run_preview()

    # ------------------------------------------------------------------ 参数收集
    def collect_options(self) -> Options:
        o = Options()
        o.strip_metadata = self.sw_meta.isChecked()
        o.keep_icc = self.cb_icc.isChecked()
        o.auto_orient = self.sw_orient.isChecked()
        o.face = self.sw_face.isChecked()
        o.face_mode = ["mosaic", "blur", "solid"][self.combo_face_mode.currentIndex()]
        o.face_strength = self.sl_face_strength.value()
        o.face_sensitivity = self.sl_face_sens.value()
        o.plate = self.sw_plate.isChecked()
        o.plate_mode = ["mosaic", "blur", "solid"][self.combo_plate_mode.currentIndex()]
        o.plate_strength = self.sl_plate_strength.value()
        o.plate_sensitivity = self.sl_plate_sens.value()
        o.text = self.sw_text.isChecked()
        o.text_mode = ["mosaic", "blur", "solid"][self.combo_text_mode.currentIndex()]
        o.text_all = self.cb_text_all.isChecked()
        o.strip_filename = self.sw_rename.isChecked()
        o.rename_pattern = self.edit_pattern.text().strip() or "IMG_{index:03d}"
        o.randomize_name = self.cb_random.isChecked()
        o.out_format = ["keep", "jpeg", "png", "webp"][self.combo_format.currentIndex()]
        o.max_side = [0, 2560, 1920, 1280, 800][self.combo_side.currentIndex()]
        o.jpeg_quality = self.sl_quality.value()
        o.output_dir = self.edit_out.text().strip()
        o.manual_boxes = list(self.manual_boxes)
        o.rotate_map = dict(self.rotate_map)
        return o

    def apply_options(self, o: Options):
        self.sw_meta.setChecked(o.strip_metadata)
        self.cb_icc.setChecked(o.keep_icc)
        self.sw_orient.setChecked(getattr(o, "auto_orient", True))
        self.sw_face.setChecked(o.face)
        self.combo_face_mode.setCurrentIndex({"mosaic": 0, "blur": 1, "solid": 2}.get(o.face_mode, 0))
        self.sl_face_strength.setValue(o.face_strength)
        self.sl_face_sens.setValue(o.face_sensitivity)
        self.sw_plate.setChecked(o.plate)
        self.combo_plate_mode.setCurrentIndex({"mosaic": 0, "blur": 1, "solid": 2}.get(o.plate_mode, 0))
        self.sl_plate_strength.setValue(o.plate_strength)
        self.sl_plate_sens.setValue(o.plate_sensitivity)
        self.sw_text.setChecked(o.text)
        self.combo_text_mode.setCurrentIndex({"mosaic": 0, "blur": 1, "solid": 2}.get(o.text_mode, 0))
        self.cb_text_all.setChecked(o.text_all)
        self.sw_rename.setChecked(o.strip_filename)
        self.edit_pattern.setText(o.rename_pattern)
        self.cb_random.setChecked(o.randomize_name)
        self.combo_format.setCurrentIndex({"keep": 0, "jpeg": 1, "png": 2, "webp": 3}.get(o.out_format, 0))
        self.combo_side.setCurrentIndex({0: 0, 2560: 1, 1920: 2, 1280: 3, 800: 4}.get(o.max_side, 0))
        self.sl_quality.setValue(o.jpeg_quality)
        self.edit_out.setText(o.output_dir)
        self.manual_boxes = list(o.manual_boxes or [])
        self.preview.set_overlay_boxes(self.manual_boxes)
        self.rotate_map = dict(getattr(o, "rotate_map", {}) or {})
        self._update_rotate_label()

    def persist_config(self):
        o = self.collect_options()
        cfg = dict(self.cfg)
        cfg["options"] = o.to_dict()
        cfg["last_dir"] = cfg.get("last_dir", "")
        try:
            cfg["win_w"] = self.width()
            cfg["win_h"] = self.height()
        except Exception:
            pass
        save_config(cfg)
        self.cfg = cfg

    def restore_config(self):
        saved = self.cfg.get("options")
        if isinstance(saved, dict):
            try:
                self.apply_options(Options.from_dict(saved))
            except Exception:
                pass
        w = self.cfg.get("win_w")
        h = self.cfg.get("win_h")
        if isinstance(w, int) and isinstance(h, int) and w > 1000 and h > 700:
            self.resize(w, h)

    # ------------------------------------------------------------------ 批量
    def start_batch(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if not self.paths:
            QMessageBox.information(self, "还没有照片", "请先把照片拖进来，或点击「添加照片」。")
            return
        options = self.collect_options()
        if not (options.strip_metadata or options.face or options.plate or options.text or options.manual_boxes):
            QMessageBox.information(self, "没有勾选任何去敏项",
                                    "至少开启一项（元数据 / 人脸 / 车牌 / 文字 / 手动框选），否则处理没有意义。")
            return
        if options.text and not models_ready():
            ret = QMessageBox.question(self, "文字识别模型未安装",
                                       "文字打码需要约 15MB 的本地识别模型，现在从国内镜像下载吗？",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ret == QMessageBox.Yes:
                self.on_download_ocr()
            return

        self.results = {}
        self.progress.setValue(0)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.settings_panel.setEnabled(False)
        self.progress.setRange(0, len(self.paths))
        self.status_label.setText("正在处理 0 / %d …" % len(self.paths))
        self._append_log("开始批量去敏：共 %d 张" % len(self.paths))
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setText(self._item_text(item.data(Qt.UserRole), "待处理"))

        self.worker = BatchWorker(self.paths, options, self)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_summary.connect(self.on_batch_done)
        self.worker.log_msg.connect(self._append_log)
        self.worker.start()

    def on_progress(self, done, total, res):
        self.progress.setValue(done)
        self.status_label.setText("正在处理 %d / %d … %s" % (done, total, os.path.basename(res.src)))
        state = "完成" if res.ok else "失败"
        row = done - 1
        if 0 <= row < self.list_widget.count():
            item = self.list_widget.item(row)
            item.setText(self._item_text(res.src, state))
            item.setForeground(QColor(theme.OK if res.ok else theme.DANGER))
        self.results[res.src] = res.to_dict()
        self.stat_done.set_value(sum(1 for r in self.results.values() if r.get("ok")))
        self.stat_fail.set_value(sum(1 for r in self.results.values() if not r.get("ok")))
        extra = []
        if res.faces:
            extra.append("人脸%d" % res.faces)
        if res.plates:
            extra.append("车牌%d" % res.plates)
        if res.texts:
            extra.append("文字%d" % res.texts)
        if res.rotated:
            extra.append("方向纠正%d°" % res.rotated)
        if res.ok:
            self._append_log("✓ %s  %s  %.2fs" % (os.path.basename(res.src), " ".join(extra) or "已去敏", res.elapsed))
        else:
            self._append_log("✕ %s  失败：%s" % (os.path.basename(res.src), res.error))

    def on_batch_done(self, summary):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.settings_panel.setEnabled(True)
        self.progress.setRange(0, max(1, summary.get("done", 0)))
        self.progress.setValue(summary.get("done", 0))
        self.status_label.setText(
            "完成 %d 张 · 失败 %d 张 · 人脸 %d 处 · 车牌 %d 处 · 文字 %d 处 · 方向纠正 %d 张"
            % (summary.get("ok", 0), summary.get("failed", 0), summary.get("faces", 0),
               summary.get("plates", 0), summary.get("texts", 0), summary.get("rotated_imgs", 0))
        )
        self._append_log("批量处理结束：成功 %d / 失败 %d" % (summary.get("ok", 0), summary.get("failed", 0)))
        options = self.collect_options()
        add_history({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": summary.get("total", 0),
            "ok": summary.get("ok", 0),
            "failed": summary.get("failed", 0),
            "options": options.to_dict(),
            "output_dir": options.output_dir,
            "sample": [os.path.basename(p) for p in self.paths[:5]],
        })
        self.refresh_history()
        # 自动把第一张成功结果展示到预览区
        ok_paths = [r["out"] for r in summary.get("results", []) if r.get("ok") and r.get("out")]
        if ok_paths and os.path.exists(ok_paths[0]):
            self._current_out_path = ok_paths[0]
            self.preview.set_image_path(ok_paths[0])
            self.btn_tab_after.setChecked(True)
            self.btn_tab_before.setChecked(False)
            self._sync_tab_style()
            self.preview_info.setText("已显示处理后的效果：%s" % os.path.basename(ok_paths[0]))
        self.worker = None

    def stop_batch(self):
        if self.worker is not None:
            self.worker.stop()
            self.status_label.setText("正在停止…已处理的照片会正常保存")
            self._append_log("已请求停止，正在收尾…")

    def open_output_dir(self):
        options = self.collect_options()
        target = options.output_dir
        if not target:
            if self.paths:
                target = os.path.join(os.path.dirname(os.path.abspath(self.paths[0])), "去敏输出")
            else:
                target = ""
        if target and os.path.isdir(target):
            os.startfile(target)
        else:
            QMessageBox.information(self, "还没有输出", "处理完成后会生成「去敏输出」文件夹。")

    # ------------------------------------------------------------------ OCR
    def refresh_ocr_label(self):
        if models_ready():
            self.ocr_label.setText("本地 OCR 模型已就绪（PP-OCRv4 轻量版，离线识别）")
            self.ocr_label.setStyleSheet("color:%s;font-size:11px;" % theme.OK)
            self.btn_ocr.setText("重新检查 OCR")
        else:
            self.ocr_label.setText("本地 OCR 模型未安装，文字打码不可用（点击下方按钮下载，约 15MB）")
            self.ocr_label.setStyleSheet("color:%s;font-size:11px;" % theme.GOLD)
            self.btn_ocr.setText("下载 OCR 模型（国内镜像）")

    def on_download_ocr(self):
        if models_ready():
            self._append_log("OCR 模型已就绪：%s" % ocr_status())
            self.refresh_ocr_label()
            return
        self.btn_ocr.setEnabled(False)
        self.status_label.setText("正在下载 OCR 模型…")
        ok = download_models(log=self._append_log)
        self.btn_ocr.setEnabled(True)
        self.refresh_ocr_label()
        if ok:
            self._append_log("OCR 模型下载完成，文字打码已可用")
            self.status_label.setText("OCR 模型就绪")
        else:
            self._append_log("OCR 模型下载失败，请检查网络后重试")
            self.status_label.setText("OCR 模型下载失败")

    # ------------------------------------------------------------------ 接口
    def on_api_toggle(self, state):
        if state:
            port = api.start(8765)
            if port:
                self._append_log("本地接口已开启：http://127.0.0.1:8765/api/status")
                self.status_label.setText("本地接口已开启（端口 8765）")
            else:
                self.cb_api.setChecked(False)
                self._append_log("端口 8765 被占用，接口启动失败")
        else:
            api.stop()
            self._append_log("本地接口已关闭")

    # ------------------------------------------------------------------ 历史 / 信息
    def refresh_history(self):
        while self.history_box.count():
            w = self.history_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        records = load_history()[:5]
        if not records:
            empty = QLabel("还没有处理记录，完成一次批量任务后会自动出现")
            empty.setObjectName("CardHint")
            empty.setWordWrap(True)
            self.history_box.addWidget(empty)
            return
        for rec in records:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)
            txt = "%s · %d 张（成功 %d）" % (rec.get("time", "")[5:16], rec.get("total", 0), rec.get("ok", 0))
            lab = QLabel(txt)
            lab.setStyleSheet("color:%s;font-size:11px;" % theme.TEXT_SUB)
            rl.addWidget(lab, 1)
            btn = make_button("套用", ghost=True)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda _c=False, r=rec: self.apply_history(r))
            rl.addWidget(btn)
            self.history_box.addWidget(row)

    def apply_history(self, rec):
        try:
            self.apply_options(Options.from_dict(rec.get("options", {})))
            self._append_log("已套用历史设置：%s（%d 张）" % (rec.get("time", ""), rec.get("total", 0)))
            self.status_label.setText("已恢复历史参数，可直接开始处理")
        except Exception as e:
            self._append_log("历史设置恢复失败：%s" % e)

    def _refresh_app_info(self):
        try:
            size = dir_size_mb(APP_ROOT)
            self.size_label.setText("程序体积 %.1f MB" % size)
            self.time_label.setText("最后更新 %s" % (latest_mtime(APP_ROOT) or time.strftime("%Y-%m-%d %H:%M")))
        except Exception:
            pass
        info = ("%s v%s\n"
                "作者：%s    微信：%s\n"
                "程序体积 %.1f MB · 最后更新 %s\n"
                "处理引擎：Pillow + OpenCV + PP-OCRv4（全部本地）\n"
                "对外接口：/api/status、/api/desensitize") % (
            APP_NAME, APP_VERSION, AUTHOR, WECHAT,
            dir_size_mb(APP_ROOT), latest_mtime(APP_ROOT) or "—")
        self.label_about.setText(info)

    def _append_log(self, text):
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText("[%s] %s" % (stamp, text))
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    # ------------------------------------------------------------------ 关闭
    def closeEvent(self, e):
        if self.worker is not None and self.worker.isRunning():
            ret = QMessageBox.question(self, "正在处理", "当前还有任务在跑，确定要退出吗？",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                e.ignore()
                return
            self.worker.stop()
            self.worker.wait(1500)
        self.persist_config()
        api.stop()
        super(MainWindow, self).closeEvent(e)


# -------------------------------------------------------------------------------
# 命令行模式（供脚本 / 其他程序调用）
# -------------------------------------------------------------------------------
def run_cli(argv):
    parser = argparse.ArgumentParser(prog="批量相片去敏工具", description="命令行批量去敏")
    parser.add_argument("--cli", action="store_true")
    parser.add_argument("--input", "-i", required=True, help="照片文件或文件夹")
    parser.add_argument("--output", "-o", default="", help="输出目录")
    parser.add_argument("--face", action="store_true", help="人脸打码")
    parser.add_argument("--plate", action="store_true", help="车牌遮挡")
    parser.add_argument("--text", action="store_true", help="文字敏感信息打码")
    parser.add_argument("--no-meta", action="store_true", help="不清除元数据（默认清除）")
    parser.add_argument("--no-orient", action="store_true", help="不自动纠正横竖方向（默认会纠正）")
    parser.add_argument("--keep-name", action="store_true", help="保留原文件名（默认重命名）")
    parser.add_argument("--max-side", type=int, default=0, help="限制最长边，0=不限制")
    args = parser.parse_args(argv)

    paths = list_images(args.input) if os.path.isdir(args.input) else [args.input]
    if not paths:
        print("没有找到可处理的照片")
        return 2
    options = Options()
    options.strip_metadata = not args.no_meta
    options.auto_orient = not args.no_orient
    options.face = args.face
    options.plate = args.plate
    options.text = args.text
    options.strip_filename = not args.keep_name
    options.max_side = args.max_side
    options.output_dir = args.output

    print("待处理 %d 张照片，开始执行…" % len(paths))
    summary = process_batch(paths, options, progress=lambda d, t, r: print("  [%d/%d] %s %s" % (
        d, t, "OK" if r.ok else "FAIL", os.path.basename(r.src)), flush=True))
    print("完成：成功 %d，失败 %d" % (summary["ok"], summary["failed"]))
    return 0 if summary["failed"] == 0 else 1


def main():
    if len(sys.argv) > 1 and "--cli" in sys.argv:
        sys.exit(run_cli(sys.argv[1:]))

    if "--api" in sys.argv:
        try:
            port = int(sys.argv[sys.argv.index("--api") + 1])
        except Exception:
            port = 8765
        started = api.start(port)
        print("本地接口：%s（Ctrl+C 退出）" % ("http://127.0.0.1:%d/api/status" % port if started else "端口被占用"))

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.QSS)
    font = QFont("Microsoft YaHei UI", 9)
    app.setFont(font)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
