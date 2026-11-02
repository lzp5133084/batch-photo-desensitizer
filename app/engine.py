# -*- coding: utf-8 -*-
"""
批量相片去敏引擎（纯本地离线）
================================
职责：把一张照片里的敏感信息清理干净，包括
  1) 元数据（EXIF / GPS 定位 / 相机型号 / 拍摄时间 / 软件名 / 缩略图 / XMP）
  2) 人脸区域（本地 Haar 级联检测后打码）
  3) 车牌区域（级联 + 形态学候选检测后遮挡）
  4) 文字敏感信息（本地 OCR 识别证件号 / 手机号 / 银行卡号 / 邮箱 / 姓名 / 地址，或整块文字区域打码）
并可选：重命名、限制最长边、统一输出格式。

本模块不依赖任何界面，可被其他程序直接 import 调用：
    from app.engine import Options, process_batch
"""
from __future__ import annotations

import os
import re
import random
import string
import time
import threading
from dataclasses import dataclass, field, asdict, fields
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

import cv2

SUPPORTED_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")

# 打码方式中文映射，供界面与日志复用
MODE_LABEL = {
    "mosaic": "马赛克",
    "blur": "高斯模糊",
    "solid": "纯色遮盖",
}


# ----------------------------------------------------------------------------
# 参数对象
# ----------------------------------------------------------------------------
@dataclass
class Options:
    """去敏参数。所有界面开关最终都落在这个对象上。"""
    # 元数据
    strip_metadata: bool = True
    keep_icc: bool = True          # 保留色彩配置（只影响色彩观感，不含隐私）
    keep_orientation: bool = True  # 处理时先按 EXIF 方向摆正，再清除方向标记
    auto_orient: bool = True       # EXIF 方向缺失时，按画面内容判断 90/180/270 并摆正

    # 人脸
    face: bool = True
    face_mode: str = "mosaic"      # mosaic / blur / solid
    face_strength: int = 60        # 1-100，马赛克块大小与模糊半径
    face_sensitivity: int = 55     # 1-100，越高越容易检出（误检也更多）

    # 车牌
    plate: bool = False            # 默认关闭：中文车牌识别率有限，避免误伤画面
    plate_mode: str = "mosaic"
    plate_strength: int = 70
    plate_sensitivity: int = 50

    # 文字
    text: bool = True
    text_mode: str = "mosaic"
    text_all: bool = False         # True = 检测到的所有文字块都打码
    text_ocr: bool = True          # True = 用本地 OCR 只打码证件号/手机号/银行卡号

    # 其他隐私
    strip_filename: bool = True    # 重命名，避免文件名本身泄露信息
    rename_pattern: str = "IMG_{index:03d}"
    randomize_name: bool = False   # 使用随机串而非序号

    # 输出
    max_side: int = 0              # 0 = 不改变尺寸
    jpeg_quality: int = 92
    out_format: str = "keep"       # keep / jpeg / png / webp
    output_dir: str = ""           # 空 = 与源目录同级的「去敏输出」文件夹

    # 手动补充遮挡框（比例坐标 0~1）：[(x1,y1,x2,y2), ...]，对所有图片生效
    manual_boxes: List[Tuple[float, float, float, float]] = field(default_factory=list)

    # 手动旋转（按文件绝对路径记录，逆时针角度 90/180/270）：{"e:\\a.jpg": 90}
    rotate_map: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Options":
        valid = {f.name for f in fields(cls)}
        clean = {k: v for k, v in (data or {}).items() if k in valid}
        return cls(**clean)


# ----------------------------------------------------------------------------
# 元数据工具
# ----------------------------------------------------------------------------
def read_metadata_summary(path: str) -> Dict[str, object]:
    """读取照片元数据概要，用于处理前后对比展示。"""
    info = {"has_exif": False, "has_gps": False, "tags": 0, "camera": "", "time": "", "software": "",
            "orientation": 1, "width": 0, "height": 0}
    try:
        with Image.open(path) as im:
            info["width"], info["height"] = im.size
            exif = im.getexif()
            if exif:
                info["tags"] = len(exif)
                info["has_exif"] = len(exif) > 0
                try:
                    info["orientation"] = int(exif.get(274, 1) or 1)
                except Exception:
                    info["orientation"] = 1
                info["camera"] = " ".join(
                    str(x) for x in [exif.get(0x010F, ""), exif.get(0x0110, "")] if x
                ).strip()
                info["time"] = str(exif.get(0x0132, "") or "")
                info["software"] = str(exif.get(0x0131, "") or "")
                try:
                    if 0x8825 in exif:
                        info["has_gps"] = True
                except Exception:
                    pass
                # 部分相机的 GPS 在 get_ifd 中
                try:
                    gps = exif.get_ifd(0x8825)
                    if gps:
                        info["has_gps"] = True
                except Exception:
                    pass
    except Exception:
        pass
    return info


def _pil_to_bgr(im: Image.Image) -> np.ndarray:
    rgb = np.asarray(im.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


# ------------------------------------------------------------------------------
# 人脸检测（多级联融合）
# ------------------------------------------------------------------------------
_CASCADE_FILES = (
    "haarcascade_frontalface_default.xml",
    "haarcascade_frontalface_alt2.xml",
    "haarcascade_profileface.xml",
)


class _Local(object):
    """线程内缓存级联模型，避免反复读 XML。"""

    def __init__(self):
        self.faces = None
        self.plate = None
        self.lock = threading.Lock()

    def face_cascades(self) -> List[cv2.CascadeClassifier]:
        if self.faces is None:
            base = cv2.data.haarcascades
            self.faces = []
            for name in _CASCADE_FILES:
                p = os.path.join(base, name)
                if os.path.exists(p):
                    c = cv2.CascadeClassifier(p)
                    if not c.empty():
                        self.faces.append(c)
        return self.faces

    def plate_cascade(self) -> Optional[cv2.CascadeClassifier]:
        if self.plate is None:
            p = os.path.join(cv2.data.haarcascades, "haarcascade_russian_plate_number.xml")
            self.plate = cv2.CascadeClassifier(p) if os.path.exists(p) else cv2.CascadeClassifier()
        return self.plate if not self.plate.empty() else None


_tls = threading.local()


def _local() -> _Local:
    obj = getattr(_tls, "obj", None)
    if obj is None:
        obj = _Local()
        _tls.obj = obj
    return obj


def _normalize_box(box, shape) -> Tuple[int, int, int, int]:
    h, w = shape[:2]
    x1, y1, x2, y2 = box
    x1 = max(0, min(w - 1, int(round(x1))))
    y1 = max(0, min(h - 1, int(round(y1))))
    x2 = max(x1 + 2, min(w, int(round(x2))))
    y2 = max(y1 + 2, min(h, int(round(y2))))
    return x1, y1, x2, y2


def detect_faces(bgr: np.ndarray, sensitivity: int = 55) -> List[Tuple[int, int, int, int]]:
    """检测人脸，返回原图坐标系下的矩形列表（已合并重叠框）。"""
    h, w = bgr.shape[:2]
    scale = 1.0
    if max(h, w) > 1100:
        scale = 1100.0 / max(h, w)
    small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)

    min_side = int(min(gray.shape[:2]) * 0.055)
    if min_side < 18:
        min_side = 18
    neighbors = int(round(6 - (max(0, min(100, sensitivity)) / 100.0) * 4))  # 6 ~ 2
    boxes = []
    for idx, cascade in enumerate(_local().face_cascades()):
        # 侧脸更容易误检，提高门槛
        nb = neighbors + 1 if idx == 2 else neighbors
        try:
            rects = cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=nb,
                                             minSize=(min_side, min_side))
        except Exception:
            continue
        for (x, y, ww, hh) in rects:
            boxes.append((x / scale, y / scale, (x + ww) / scale, (y + hh) / scale))
    norm = [_normalize_box(b, bgr.shape) for b in boxes]
    # 误检抑制：
    #   · 长宽比异常（横向长条/竖直条）不是人脸
    #   · 面积超过画面 60% 的框基本是误检（真正的大头照不会这么大）
    #   · 所有框都做肤色校验，但灰度图跳过（老照片/黑白扫描没有色彩信息）
    img_area = float(bgr.shape[0] * bgr.shape[1])
    gray_like = float(bgr.max()) < 1e-6 or cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).std() < 1.0
    filtered = []
    for b in norm:
        bw = max(1, b[2] - b[0])
        bh = max(1, b[3] - b[1])
        ratio = bw / float(bh)
        if ratio < 0.5 or ratio > 2.0:
            continue
        area = bw * bh
        if area > img_area * 0.60:
            continue
        patch = bgr[b[1]:b[3], b[0]:b[2]]
        if patch.size > 0 and not gray_like:
            ycc = cv2.cvtColor(patch, cv2.COLOR_BGR2YCrCb)
            cr, cb = ycc[:, :, 1], ycc[:, :, 2]
            # 收紧到标准肤色区间：票据纸张、灰白墙面常落在旧区间里，造成大量误检
            skin_arr = (cr > 133) & (cr < 173) & (cb > 77) & (cb < 127)
            skin = skin_arr.mean()
            # 小框要求更高，大框适度放宽（人脸包含头发、衣物边缘）
            need = 0.10 if area < img_area * 0.05 else 0.08
            if skin < need:
                continue
            # 几乎全是"肤色"、又没有纹理的，是纸张/墙面，不是人脸
            gray_patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
            edge = cv2.Canny(gray_patch, 80, 160).mean() / 255.0
            if skin > 0.93 and edge < 0.06:
                continue
        filtered.append(b)
    return _merge_boxes(filtered, iou_thr=0.25)


def _merge_boxes(boxes: List[Tuple[int, int, int, int]], iou_thr: float = 0.3) -> List[Tuple[int, int, int, int]]:
    """非极大值抑制：重叠的检测框只保留最大的那个，避免框被越并越大。"""
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    kept: List[Tuple[int, int, int, int]] = []
    for b in boxes:
        drop = False
        for k in kept:
            ix1, iy1 = max(b[0], k[0]), max(b[1], k[1])
            ix2, iy2 = min(b[2], k[2]), min(b[3], k[3])
            if ix2 <= ix1 or iy2 <= iy1:
                continue
            inter = (ix2 - ix1) * (iy2 - iy1)
            area_b = (b[2] - b[0]) * (b[3] - b[1])
            area_k = (k[2] - k[0]) * (k[3] - k[1])
            iou = inter / float(area_b + area_k - inter + 1e-6)
            cx = (b[0] + b[2]) / 2.0
            cy = (b[1] + b[3]) / 2.0
            inside = k[0] <= cx <= k[2] and k[1] <= cy <= k[3]
            if iou > iou_thr or inside:
                drop = True
                break
        if not drop:
            kept.append(b)
    return kept


# ------------------------------------------------------------------------------
# 车牌检测
# ------------------------------------------------------------------------------
def detect_plates(bgr: np.ndarray, sensitivity: int = 50) -> List[Tuple[int, int, int, int]]:
    """车牌候选检测：按车牌颜色（蓝/黄/绿牌）+ 长条比例找候选，再用级联结果兜底。
    灵敏度越高，对面积和长宽比的要求越宽松（误检也更多）。"""
    h, w = bgr.shape[:2]
    scale = 1.0
    if max(h, w) > 1400:
        scale = 1400.0 / max(h, w)
    small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr
    sen = max(0, min(100, sensitivity)) / 100.0
    sh, sw = small.shape[:2]

    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    masks = {
        "蓝牌": cv2.inRange(hsv, np.array([95, 60, 45], np.uint8), np.array([140, 255, 255], np.uint8)),
        "黄牌": cv2.inRange(hsv, np.array([15, 70, 80], np.uint8), np.array([38, 255, 255], np.uint8)),
        "绿牌": cv2.inRange(hsv, np.array([40, 60, 60], np.uint8), np.array([90, 255, 255], np.uint8)),
    }

    boxes: List[Tuple[float, float, float, float]] = []
    ratio_min = 2.0 - sen * 0.4
    ratio_max = 6.0 + sen * 1.2
    area_min = sw * sh * (0.0035 - sen * 0.002)
    for name, mask in masks.items():
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (21, 9)))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            x, y, ww, hh = cv2.boundingRect(c)
            if hh <= 0 or ww <= 0:
                continue
            ratio = ww / float(hh)
            if not (ratio_min <= ratio <= ratio_max):
                continue
            box_area = ww * hh
            if box_area < area_min or ww < sw * 0.06:
                continue
            fill = cv2.contourArea(c) / float(box_area)
            if fill < 0.35:
                continue
            boxes.append((x, y, x + ww, y + hh))

    # 级联兜底：只接受颜色占比足够高的结果（避免把车窗、广告牌当车牌）
    cascade = _local().plate_cascade()
    if cascade is not None:
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        min_w = max(30, int(sw * 0.07))
        try:
            rects = cascade.detectMultiScale(gray, scaleFactor=1.1,
                                             minNeighbors=int(round(6 - sen * 3)),
                                             minSize=(min_w, int(min_w * 0.3)))
            for (x, y, ww, hh) in rects:
                patch = hsv[y:y + hh, x:x + ww]
                if patch.size == 0:
                    continue
                hhsv = patch[:, :, 0]
                is_blue = np.logical_and(hhsv >= 95, hhsv <= 140).mean()
                is_yellow = np.logical_and(hhsv >= 15, hhsv <= 38).mean()
                is_green = np.logical_and(hhsv >= 40, hhsv <= 90).mean()
                if max(is_blue, is_yellow, is_green) < 0.25:
                    continue
                boxes.append((x, y, x + ww, y + hh))
        except Exception:
            pass

    out = []
    for b in boxes:
        out.append(_normalize_box((b[0] / scale, b[1] / scale, b[2] / scale, b[3] / scale), bgr.shape))
    out = _merge_boxes(out, iou_thr=0.3)

    def color_ratio(box):
        x1, y1, x2, y2 = box
        patch = bgr[max(0, y1):y2, max(0, x1):x2]
        if patch.size == 0:
            return 0.0
        ph = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        s, v = ph[:, :, 1], ph[:, :, 2]
        hch = ph[:, :, 0]
        valid = (s >= 60) & (v >= 45)
        blue = valid & (hch >= 95) & (hch <= 140)
        yellow = valid & (hch >= 15) & (hch <= 38)
        green = valid & (hch >= 40) & (hch <= 90)
        return float(max(blue.mean(), yellow.mean(), green.mean()))

    ocr = _get_ocr()
    if out:
        if ocr is not None:
            verified = []
            for box in out:
                if color_ratio(box) < 0.30:
                    continue
                x1, y1, x2, y2 = box
                crop = bgr[y1:y2, x1:x2]
                if crop.size == 0:
                    continue
                try:
                    text, conf = ocr.recognize_crop(crop)
                except Exception:
                    text, conf = "", 0.0
                if conf > 0.5 and _looks_like_plate(text):
                    verified.append(box)
            out = verified
        else:
            out = [b for b in out if color_ratio(b) >= 0.40]
    return out


def _looks_like_plate(text: str) -> bool:
    """车牌文本格式：汉字（省份简称）+ 字母 + 5~6 位数字/字母，可选中间的点。"""
    import re
    clean = text.replace(" ", "").replace("·", "").replace("•", "").replace(".", "")
    return bool(re.match("^[\\u4e00-\\u9fa5][A-Z][A-Z0-9]{4,6}$", clean, re.I))


# ------------------------------------------------------------------------------
# 文字区域检测（OCR 不可用时的兜底 / 整块文字打码）
# ------------------------------------------------------------------------------
def detect_text_regions(bgr: np.ndarray, sensitivity: int = 50) -> List[Tuple[int, int, int, int]]:
    """通用文字块检测：形态学梯度 + 横向膨胀，只找「成行的横向文字块」。"""
    h, w = bgr.shape[:2]
    scale = 1.0
    if max(h, w) > 1300:
        scale = 1300.0 / max(h, w)
    small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (19, 3))
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    sen = max(0, min(100, sensitivity)) / 100.0
    min_h = max(8, int(small.shape[0] * (0.018 - sen * 0.008)))
    boxes = []
    for c in contours:
        x, y, ww, hh = cv2.boundingRect(c)
        if hh < min_h or ww < max(10, hh * 1.6):
            continue
        if ww * hh < small.shape[0] * small.shape[1] * 0.0015:
            continue
        if ww / float(hh) > 12:
            continue
        boxes.append(_normalize_box((x / scale, y / scale, (x + ww) / scale, (y + hh) / scale), bgr.shape))
    return _merge_boxes(boxes, iou_thr=0.1)


# ------------------------------------------------------------------------------
# 打码渲染
# ------------------------------------------------------------------------------
def _expand(box, shape, pad_ratio: float = 0.10):
    x1, y1, x2, y2 = box
    w = x2 - x1
    h = y2 - y1
    pad = int(max(3, min(w, h) * pad_ratio))
    return _normalize_box((x1 - pad, y1 - pad, x2 + pad, y2 + pad), shape)


def apply_mask(im: Image.Image, box, mode: str = "mosaic", strength: int = 60,
               color=(18, 22, 30)) -> None:
    """在 PIL 图上对指定区域打码（就地修改）。"""
    x1, y1, x2, y2 = [int(v) for v in box]
    if x2 - x1 < 3 or y2 - y1 < 3:
        return
    strength = max(1, min(100, int(strength)))
    region = im.crop((x1, y1, x2, y2))

    if mode == "blur":
        radius = max(4.0, min(90.0, min(region.size) * (0.04 + strength / 100.0 * 0.16)))
        im.paste(region.filter(ImageFilter.GaussianBlur(radius)), (x1, y1))
        return

    if mode == "solid":
        ImageDraw.Draw(im).rectangle((x1, y1, x2, y2), fill=color)
        return

    # 马赛克：按绝对像素切块（6~48px），保证车牌字符、五官都被抹掉，不随区域大小漂移
    block = int(round(6 + strength / 100.0 * 34))
    block = max(6, min(48, block))
    small_w = max(2, region.size[0] // block)
    small_h = max(2, region.size[1] // block)
    tiny = region.resize((small_w, small_h), Image.BILINEAR)
    im.paste(tiny.resize(region.size, Image.NEAREST), (x1, y1))


# ------------------------------------------------------------------------------
# OCR 适配（延迟导入，缺模型自动降级）
# ------------------------------------------------------------------------------
_ocr_instance = None
_ocr_error = ""


def _get_ocr():
    global _ocr_instance, _ocr_error
    if _ocr_instance is not None:
        return _ocr_instance
    try:
        from .ocr_local import LocalOCR
        _ocr_instance = LocalOCR()
        if not _ocr_instance.available():
            _ocr_error = _ocr_instance.status_text()
            _ocr_instance = None
    except Exception as e:  # 缺少依赖或模型
        _ocr_error = "OCR 模块不可用：%s" % e
        _ocr_instance = None
    return _ocr_instance


def ocr_available() -> bool:
    return _get_ocr() is not None


def ocr_status() -> str:
    if _get_ocr() is not None:
        return "本地 OCR 就绪"
    return _ocr_error or "本地 OCR 未就绪"


SENSITIVE_PATTERNS = (
    ("身份证号", r"(?<!\d)[1-9]\d{5}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|1[0-9]|2[0-9]|3[0-1])\d{3}[\dXx](?!\d)"),
    ("手机号",   r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    ("邮箱",     r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    ("银行卡号", r"(?<!\d)\d{16,19}(?!\d)"),
)

# 这些词出现时，同一行里的长数字串是身份证 / 流水号 / 单号 / 时间，不是银行卡
_BANK_CTX_RX = re.compile(
    "身份证|证件|流水|发票|单据|票据|门诊|住院|登记|编号|病历|结算|就诊|"
    "代码|合同|保单|报告|条码|二维码|日期|时间|床位|科室|病区")

# 号码类字段标签：即使数字被 OCR 认错（位数不对），也把整行遮掉，避免泄漏
_NUM_LABEL_RX = re.compile(
    "身份证|证件号码|证件号|银行卡|卡号|账号|帐号|账户|手机号|手机号码|"
    "联系电话|联系方式|电子邮箱|邮箱")

# ---------- 中文姓氏表 ----------
# 单姓只放常用、可靠的字，避免把机构常用字（国/文/成 等）之外的字也当姓
_CN_SINGLE = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计戴宋茅庞熊纪舒屈项祝董梁杜阮"
    "蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万"
    "支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇"
    "邢滑裴陆荣翁荀羊惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗"
    "山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘厉戎祖武符刘景詹束龙叶幸"
    "司韶郜黎蓟薄印宿白怀蒲台从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘"
    "党翟谭贡劳逄姬申扶堵冉宰郦雍桑桂濮牛寿通边扈燕冀郏浦尚农温别"
    "庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡寇"
    "广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜"
    "养鞠须丰巢关蒯相查荆红游竺权逯盖益桓公"
)
# 常见复姓（两字）
_CN_DOUBLE = set((
    "欧阳", "太史", "端木", "上官", "司马", "东方", "独孤", "南宫", "万俟", "闻人",
    "夏侯", "诸葛", "尉迟", "公羊", "赫连", "皇甫", "澹台", "公冶", "宗政", "濮阳",
    "淳于", "单于", "太叔", "申屠", "公孙", "仲孙", "轩辕", "令狐", "钟离", "宇文",
    "长孙", "慕容", "鲜于", "闾丘", "司徒", "司空", "亓官", "司寇", "子车", "颛孙",
    "巫马", "公西", "漆雕", "乐正", "壤驷", "公良", "拓跋", "夹谷", "宰父", "谷梁",
    "段干", "百里", "东郭", "南门", "呼延", "归海", "羊舌", "微生", "梁丘", "左丘",
    "东门", "西门", "第五", "言福", "闻人",
))
_CN_OLD_SURNAMES = _CN_SINGLE  # 兼容旧引用

# ---------- 姓名/地址上下文触发关键词 ----------
# 姓名标签：表单里最可靠的线索。标签后面跟的短文本直接当人名遮掉，
# 这样即使 OCR 把"罗小兵"认成"罗小乓"、或名字里含生僻字，也不会漏。
_NAME_LABELS = (
    "患者姓名", "联系人姓名", "用户姓名", "经办人姓名", "参保人姓名",
    "法定代表人", "法人代表", "授权代表", "被委托人", "签署人", "领取人",
    "持卡人", "收款人", "付款人", "交款人", "领款人", "复核人", "审核人",
    "审批人", "制单人", "开票人", "记账人", "经办人", "承办人", "申请人",
    "负责人", "联系人", "收件人", "寄件人", "发件人", "送达人", "签收人",
    "提交人", "填报人", "登记人", "申报人", "操作员",
    "开户名", "户名", "用户名", "姓名", "患者", "参保人", "被保险人", "投保人",
    "受益人", "就诊人", "病人", "病员",
    "医师签名", "医生签名", "护士签名", "签名", "签字",
    "Name", "Author", "Contact",
)
# 长标签优先匹配，避免"患者姓名"被"患者"抢先截断
_NAME_LABELS = tuple(sorted(_NAME_LABELS, key=len, reverse=True))

# 标签后面如果紧跟的是另一个字段名（表头并列），说明这里没有真正的人名
_NAME_FOLLOW_LABELS = (
    "姓名", "名称", "性别", "年龄", "地址", "电话", "手机", "邮编", "邮箱",
    "单位", "公司", "部门", "职务", "民族", "婚姻", "学历", "职业", "籍贯",
    "日期", "时间", "编号", "号码", "证件", "身份", "备注", "科室", "病区",
    "自助", "机器", "系统", "窗口",
)

_ADDR_TRIGGERS = (
    "地址", "住址", "通讯地址", "邮寄地址", "收件地址", "寄件地址", "送件地址",
    "住所地", "户籍地", "常住地", "居住地址", "居住",
    "Address", "Mail Address", "邮寄", "收件",
)

# 正文排除词（职称、动词、称谓）
_NAME_EXCLUDES = (
    "教授", "专家", "研究员", "工程师", "老师", "校长", "院长", "主任", "经理",
    "医生", "护士", "律师", "法官", "书记", "秘书长", "会长", "局长", "部长",
    "指导", "悉心", "表示", "研究", "设计", "开发", "生产", "销售", "采购",
    "先生", "女士", "小姐", "同志",
    "经过", "经营", "经济", "经历", "业务", "办理", "流程", "说明", "程序", "工程",
)

# 字段词：这些不是姓名，是表单标签
_FIELD_WORDS = set((
    "姓名", "名称", "性别", "年龄", "地址", "住址", "电话", "手机", "邮箱", "备注",
    "单位", "公司", "部门", "职务", "日期", "时间", "签名", "签字", "身份", "证件",
    "号码", "卡号", "账户", "账号", "银行", "开户", "联系", "收件", "寄件", "籍贯",
    "民族", "婚姻", "学历", "专业", "工作", "编号", "金额", "用途", "说明",
    "科室", "病区", "门诊", "住院", "患者", "参保", "保险", "基金", "支付",
    "自费", "自付", "费用", "合计", "总计", "项目", "数量", "备注",
))

# 表单/票据里高频、但人名里极少出现的字：出现即判为「不是人名」
# 注意不要收进常用名字用字（明/华/国/军/伟/建/红/玲/梅/芳…）
_NAME_BAD_CHARS = set(
    "费用额金章间证称时别书目院县区市省号价率元注付收印盖签期码卡行款账"
    "户部单总表联电邮编类型状态说办业务发票据件度量图字科处所站馆厅室组社团"
    "厂店校局委署查测定护诊疗血压常检验粪便无自合职工作农民退缴预数计算银"
    "脑网络病患医疗床次费"
)

# 表单/票据里的高频词，整行或冒号后出现时直接排除
_NAME_BAD_WORDS = (
    "农民", "居民", "职工", "员工", "患者", "病人", "医院", "医生", "护士",
    "银行", "公司", "单位", "学校", "学院", "酒店", "汉族", "已婚", "未婚",
    "其他", "合计", "总计", "费用", "金额", "项目", "名称", "日期", "时间",
    "门诊", "住院", "科室", "病区", "护理", "检查", "化验", "治疗", "手术",
    "药品", "处方", "报告", "说明", "备注", "本人", "签字", "签名", "盖章",
    "明细", "明确", "证明", "表明", "声明", "注明", "明示", "司机",
)

# 机构/地名尾词：候选附近出现这些词，说明它不是人名
_ORG_TAIL = (
    "市", "区", "省", "县", "路", "街", "号", "公司", "有限", "集团", "银行",
    "大学", "学院", "学校", "医院", "酒店", "宾馆", "超市", "广场", "大厦",
    "中心", "餐厅", "门店", "店", "厂", "局", "所", "站", "馆", "厅",
)


def _is_surname(cand: str) -> bool:
    """候选姓名是否以常见姓氏开头（复姓或单姓）。"""
    if len(cand) >= 2 and cand[:2] in _CN_DOUBLE:
        return True
    return bool(cand) and cand[0] in _CN_SINGLE


def _has_name_bad(cand: str) -> bool:
    """候选里是否含表单/票据用字，或整词就是表单词。"""
    if any(ch in _NAME_BAD_CHARS for ch in cand):
        return True
    if cand in _FIELD_WORDS or cand in _NAME_BAD_WORDS:
        return True
    if any(w in cand for w in _NAME_BAD_WORDS):
        return True
    return any(cand.startswith(ex) or ex in cand for ex in _NAME_EXCLUDES)


def _pick_name(han: str) -> str:
    """从一段汉字里挑出「像人名」的 2~4 字词；挑不出返回空串。

    与旧版的区别：不再只要首字是姓就算，
    还要求整词不含表单用字（费/金额/项目/时间/盖章…），
    这样「收费专用章」「金额（元）」「项目名称」不会再被当成人名。
    """
    if not han or len(han) < 2:
        return ""
    body = han
    if len(body) >= 3 and body[-1] in "男女":
        body = body[:-1]          # "罗小兵男" → "罗小兵"
    if 2 <= len(body) <= 4:
        if _is_surname(body) and not _has_name_bad(body):
            return body
        return ""
    # 值区里混了别的内容时，只看开头 2~3 字
    for L in (3, 2):
        cand = han[:L]
        if _is_surname(cand) and not _has_name_bad(cand):
            return cand
    return ""


def _look_like_name(text: str, trigger_hint: bool = False) -> bool:
    """判断 OCR 文本里是否含疑似中文姓名。

    规则（按可靠度排序）：
      1) 出现「姓名/患者姓名/收款人/交款人/医师签名…」等标签，且标签后有内容 → 直接认定
         （标签比人名本身好认，OCR 把名字认错也不影响遮住）
      2) 有冒号且冒号后面的值是一个完整的名字形态 → 认定
      3) 整行就是 2~4 个汉字（可带男女尾字）且首字是姓、不含表单用字 → 认定
    其余（金额/项目名称/收费专用章/打印时间/性别/合江县…）一律不认。
    """
    import re
    raw = text or ""
    if re.search(r"[A-Z][a-zA-Z]{1,}\s+[A-Z][a-zA-Z]{1,}", raw):
        return True

    # 1) 姓名标签
    for kw in _NAME_LABELS:
        idx = raw.find(kw)
        if idx < 0:
            continue
        zone = raw[idx + len(kw):]
        if re.search("[，。,、]", zone):
            continue          # 带句读的是正文，不是表单里的人名
        han = re.sub(r"[^\u4e00-\u9fa5]", "", zone)
        if len(han) < 2 or len(han) > 5:
            continue          # 人名 2~4 字，超过 5 字基本是把整句正文当成了值
        if any(han.startswith(w) for w in _NAME_FOLLOW_LABELS):
            continue          # 表头并列字段，如"姓名 性别 年龄"
        if _has_name_bad(han):
            continue          # "患者签字""交款人一金用"这类：值区其实是别的字段
        return True

    # 2) 冒号/逗号/分号后面的值（OCR 经常把冒号认成 ; ，）
    #    只对表单短行生效：长句子里逗号后的片段经常以姓字开头（如"红细胞体"），不能猜
    if len(re.sub(r"[^\u4e00-\u9fa5]", "", raw)) <= 12:
        parts = re.split(r"[：:，,；;]", raw)
        if len(parts) >= 2:
            han = re.sub(r"[^\u4e00-\u9fa5]", "", parts[-1])
            if 2 <= len(han) <= 4 and _pick_name(han):
                return True

    # 3) 独立成行的人名
    han_all = re.sub(r"[^\u4e00-\u9fa5]", "", raw)
    if 2 <= len(han_all) <= 4 and _pick_name(han_all):
        return True
    return False


def _look_like_address(text: str, trigger_hint: bool) -> bool:
    """判断一行里是否含疑似地址。
    触发关键词或省市区街道结构至少满足一个。
    """
    import re
    has_trigger = any(kw in text for kw in _ADDR_TRIGGERS)
    # 中文地址：至少含 省/市/区/县/街道/镇/乡/路/街/巷/栋/单元/室/幢/层 之一
    # 注意：不要让"号"单独命中（避免"手机号/银行卡号/身份证号"这种字段名被误伤）
    addr_kw = ("省", "市", "区", "县", "街道", "镇", "乡", "路", "街", "巷",
               "栋", "单元", "室", "幢", "层")
    has_structure = any(k in text for k in addr_kw)
    # 门牌号结构：必须有"数字/字母 + 号"，不能是"手机号/身份证号/银行卡号"这种字段名
    m = re.search(r"(?:[\u4e00-\u9fa5]{1,6}?[路街巷弄])?[\dA-Za-z\-]{1,6}号", text)
    has_house = bool(m) and not re.match(r"^[^\d]*号[：:：]?[^\d]*$", text.strip())
    # 邮编结构：6 位数字（可选，作为辅助证据）
    has_zip = bool(re.search(r"(?<!\d)\d{6}(?!\d)", text))

    # 排除：纯 label 行（只有"xxx："没有内容）
    stripped = text.strip()
    if re.match(r"^[\u4e00-\u9fa5]{2,10}[：:]$", stripped):
        return False

    # 有"地址/住址"等标签，且标签后确实跟了实质内容 → 直接认定
    for kw in _ADDR_TRIGGERS:
        idx = text.find(kw)
        if idx >= 0:
            after = re.sub(r"[^\u4e00-\u9fa5]", "", text[idx + len(kw):])
            if len(after) >= 5:
                return True

    if has_trigger and (has_structure or has_house or has_zip):
        return True
    # 没有触发关键词时，必须同时有 市/区 + 门牌号 才认
    if (not has_trigger) and has_structure and has_house:
        return True
    return False


def find_sensitive_text(bgr: np.ndarray) -> List[Tuple[Tuple[int, int, int, int], str, str]]:
    """用本地 OCR 找敏感文字，返回 [(框, 文本, 命中类型)]。
    覆盖：身份证号 / 手机号 / 银行卡号 / 邮箱 / 姓名 / 地址。
    """
    ocr = _get_ocr()
    if ocr is None:
        return []
    import re
    hits = []
    seen = set()  # 同一行不重复命中多类
    for box, text, score in ocr.recognize(bgr):
        if not text:
            continue
        box_key = (box[0], box[1], box[2], box[3])
        clean = text.replace(" ", "").replace("-", "")
        kind = None

        for name, pattern in SENSITIVE_PATTERNS:
            # 银行卡要避开身份证/流水号/单号/时间这类同行上下文，否则误伤一大片
            if name == "银行卡号" and _BANK_CTX_RX.search(text):
                continue
            if re.search(pattern, clean):
                kind = name
                break

        # 号码字段标签 + 数字（数字认错也照样遮，宁多勿漏）
        if kind is None and _NUM_LABEL_RX.search(text) and re.search(r"\d{5,}", clean):
            kind = "证件号码"

        if kind is None:
            # 姓名 / 地址走启发式
            name_hit = _look_like_name(text, any(kw in text for kw in _NAME_LABELS))
            addr_hit = _look_like_address(text, any(kw in text for kw in _ADDR_TRIGGERS))
            # 一行里姓名优先（避免把"姓名：张三 地址：xxxxx"整行当地址打码）
            if name_hit and not addr_hit:
                kind = "姓名"
            elif addr_hit and not name_hit:
                kind = "地址"
            elif name_hit and addr_hit:
                kind = "姓名+地址"  # 整块打码

        if kind and box_key not in seen:
            seen.add(box_key)
            hits.append((box, text, kind))
    return hits


# ------------------------------------------------------------------------------
# 单张处理
# ------------------------------------------------------------------------------
class ProcessResult(object):
    def __init__(self, src: str):
        self.src = src
        self.out: Optional[str] = None
        self.ok = False
        self.error = ""
        self.elapsed = 0.0
        self.faces = 0
        self.plates = 0
        self.texts = 0
        self.rotated = 0
        self.meta_before = {}
        self.meta_after = {}
        self.in_bytes = 0
        self.out_bytes = 0

    def to_dict(self) -> dict:
        return {
            "src": self.src, "out": self.out, "ok": self.ok, "error": self.error,
            "elapsed": round(self.elapsed, 3), "faces": self.faces, "plates": self.plates,
            "texts": self.texts, "rotated": self.rotated, "in_bytes": self.in_bytes, "out_bytes": self.out_bytes,
            "meta_before": self.meta_before, "meta_after": self.meta_after,
        }


def _open_image(path: str) -> Image.Image:
    im = Image.open(path)
    im.load()
    # CMYK 之类的印刷色彩模式统一转成 RGB，避免保存后偏色/失败
    if im.mode in ("CMYK", "YCbCr", "LAB", "HSV"):
        im = im.convert("RGB")
    return im


# ------------------------------------------------------------------------------
# 方向纠正（EXIF + 画面内容）
# ------------------------------------------------------------------------------
def _rot_bgr(bgr: np.ndarray, deg: int) -> np.ndarray:
    """按逆时针角度旋转 BGR 图，方向与 PIL rotate 一致。"""
    k = {0: 0, 90: 1, 180: 2, 270: 3}.get(deg, 0)
    return np.rot90(bgr, k) if k else bgr


def _face_area_ratio(bgr: np.ndarray) -> float:
    """检出人脸的总面积占画面比例。正立照片的脸最容易检出。"""
    try:
        boxes = detect_faces(bgr, 55)
    except Exception:
        return 0.0
    if not boxes:
        return 0.0
    h, w = bgr.shape[:2]
    area = sum(max(0, b[2] - b[0]) * max(0, b[3] - b[1]) for b in boxes)
    return area / float(max(1, w * h))


def _text_quality(bgr: np.ndarray) -> float:
    """OCR 读出的有效字符量（字符数 × 置信度），方向正确时数值最高。"""
    ocr = _get_ocr()
    if ocr is None:
        return 0.0
    total = 0.0
    try:
        for _box, text, conf in ocr.recognize(bgr):
            t = (text or "").strip()
            if len(t) >= 2:
                total += len(t) * max(0.0, float(conf))
    except Exception:
        return 0.0
    return total


def detect_content_rotation(im: Image.Image, options: Options,
                            log: Optional[Callable[[str], None]] = None) -> Tuple[int, str]:
    """EXIF 没写方向时，按画面内容判断需要逆时针旋转多少度才能摆正。

    思路：人脸在正立时最容易检出；文字在方向正确时能读出最多字符。
    两者都拿不到明确证据时返回 (0, "")，宁可不转也不乱转。
    """
    if not options.auto_orient:
        return 0, ""
    try:
        probe = im.copy()
        probe.thumbnail((1400, 1400), Image.LANCZOS)
        bgr0 = _pil_to_bgr(probe)
    except Exception:
        return 0, ""

    # 1) 人脸证据（阈值提高，宁可漏转也不误转；漏转可以手动补）
    face0 = _face_area_ratio(bgr0) if options.face else 0.0
    if face0 >= 0.025:
        return 0, "人脸正立"
    if options.face and face0 < 0.025:
        best_ratio, best_deg = 0.0, 0
        for deg in (90, 180, 270):
            r = _face_area_ratio(_rot_bgr(bgr0, deg))
            if r > best_ratio:
                best_ratio, best_deg = r, deg
        if best_deg and best_ratio >= 0.025 and best_ratio >= max(0.025, face0 * 1.4):
            return best_deg, "人脸正立"

    # 2) 文字证据（人脸没线索时才做，4 个方向对比字符量）
    if ocr_available():
        q0 = _text_quality(bgr0)
        if q0 >= 40:                       # 已经读得很顺，方向正确
            return 0, ""
        best_q, best_deg = q0, 0
        for deg in (90, 180, 270):
            q = _text_quality(_rot_bgr(bgr0, deg))
            if q > best_q:
                best_q, best_deg = q, deg
        # 提升必须非常明显才转，避免正常照片被文字噪点带歪
        if best_deg and best_q >= 30 and best_q >= max(60.0, q0 * 2.5):
            return best_deg, "文字"

    return 0, ""


def desensitize(im: Image.Image, options: Options, log: Optional[Callable[[str], None]] = None,
                manual_deg: int = 0) -> Tuple[Image.Image, Dict[str, int]]:
    """对内存中的 PIL 图像执行去敏，返回 (处理后图像, 统计)。"""
    stats = {"faces": 0, "plates": 0, "texts": 0, "rotated": 0}

    # 1) 方向：EXIF 写了方向就按 EXIF 摆正；EXIF 没写就按画面内容判断
    ori = 1
    try:
        ori = int(im.getexif().get(274, 1) or 1)
    except Exception:
        ori = 1

    if options.keep_orientation and ori in (2, 3, 4, 5, 6, 7, 8):
        try:
            im = ImageOps.exif_transpose(im)
        except Exception:
            pass

    rotated = 0
    mdeg = int(manual_deg or 0) % 360
    if mdeg in (90, 180, 270):
        # 用户手动指定角度，优先级最高，不再猜
        im = im.rotate(mdeg, expand=True, resample=Image.BICUBIC)
        rotated = mdeg
        if log:
            log("按手动设置旋转 %d°" % mdeg)
    elif options.auto_orient and ori in (0, 1):
        deg, why = detect_content_rotation(im, options, log)
        if deg:
            im = im.rotate(deg, expand=True, resample=Image.BICUBIC)
            rotated = deg
            if log:
                log("检测到照片方向偏转，已旋转 %d° 摆正（依据：%s）" % (deg, why))
    stats["rotated"] = rotated

    work = im
    bgr = _pil_to_bgr(im)

    boxes_face: List[Tuple[int, int, int, int]] = []
    boxes_plate: List[Tuple[int, int, int, int]] = []
    boxes_text: List[Tuple[int, int, int, int]] = []

    if options.face:
        boxes_face = detect_faces(bgr, options.face_sensitivity)
    if options.plate:
        boxes_plate = detect_plates(bgr, options.plate_sensitivity)
    if options.text:
        if options.text_all or not options.text_ocr:
            boxes_text = detect_text_regions(bgr, 50)
        else:
            for box, text, kind in find_sensitive_text(bgr):
                boxes_text.append(box)
                if log:
                    log("识别到%s：%s" % (kind, text))

    # 2) 逐区域打码
    for b in boxes_face:
        apply_mask(work, _expand(b, (work.size[1], work.size[0]), 0.08), options.face_mode, options.face_strength)
    for b in boxes_plate:
        apply_mask(work, _expand(b, (work.size[1], work.size[0]), 0.10), options.plate_mode, options.plate_strength)
    for b in boxes_text:
        apply_mask(work, _expand(b, (work.size[1], work.size[0]), 0.08), options.text_mode, 60)

    # 3) 手动框（比例坐标 → 像素）
    W, H = work.size
    for (rx1, ry1, rx2, ry2) in options.manual_boxes:
        b = (int(rx1 * W), int(ry1 * H), int(rx2 * W), int(ry2 * H))
        apply_mask(work, b, "solid", 100)

    # 4) 尺寸限制
    if options.max_side and max(work.size) > options.max_side:
        ratio = options.max_side / float(max(work.size))
        new_size = (max(1, int(work.size[0] * ratio)), max(1, int(work.size[1] * ratio)))
        work = work.resize(new_size, Image.LANCZOS)

    stats["faces"] = len(boxes_face)
    stats["plates"] = len(boxes_plate)
    stats["texts"] = len(boxes_text)
    return work, stats


def resolve_format(src: str, options: Options) -> Tuple[str, str]:
    """返回 (Pillow 格式, 目标扩展名)。"""
    if options.out_format in ("jpeg", "png", "webp"):
        return options.out_format, {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}[options.out_format]
    ext = os.path.splitext(src)[1].lower().lstrip(".")
    if ext in ("jpg", "jpeg"):
        return "jpeg", ".jpg"
    if ext == "png":
        return "png", ".png"
    if ext == "webp":
        return "webp", ".webp"
    return "jpeg", ".jpg"   # bmp / tiff 等统一转成 JPEG，兼容性最好


def _save(im: Image.Image, out_path: str, options: Options, fmt: str) -> None:
    """保存图片，并在保存时确保不写入任何元数据。"""
    icc = im.info.get("icc_profile") if options.keep_icc else None

    if fmt == "jpeg":
        save_kw = dict(format="JPEG", quality=int(options.jpeg_quality), optimize=True)
        if icc:
            save_kw["icc_profile"] = icc
        im.convert("RGB").save(out_path, **save_kw)   # 不传 exif => 彻底不写 EXIF
    elif fmt == "png":
        save_kw = dict(format="PNG", optimize=True)
        if icc:
            save_kw["icc_profile"] = icc
        im.save(out_path, **save_kw)
    elif fmt == "webp":
        save_kw = dict(format="WEBP", quality=int(options.jpeg_quality), method=4)
        if icc:
            save_kw["icc_profile"] = icc
        im.convert("RGB").save(out_path, **save_kw)
    else:
        im.convert("RGB").save(out_path, format="JPEG", quality=int(options.jpeg_quality))


def _target_name(index: int, src: str, options: Options, used: set) -> str:
    _fmt, ext = resolve_format(src, options)
    if not options.strip_filename:
        return os.path.basename(src)

    if options.randomize_name:
        stem = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(14))
    else:
        pattern = options.rename_pattern or "IMG_{index:03d}"
        try:
            stem = pattern.format(index=index + 1, date=time.strftime("%Y%m%d"),
                                  time=time.strftime("%H%M%S"), rand=random.randint(1000, 9999))
        except Exception:
            stem = "IMG_%03d" % (index + 1)
    name = stem + ext
    n = 1
    while name.lower() in used:
        name = "%s_%d%s" % (stem, n, ext)
        n += 1
    return name


def default_output_dir(src: str, options: Options) -> str:
    if options.output_dir:
        return options.output_dir
    base = os.path.dirname(os.path.abspath(src))
    return os.path.join(base, "去敏输出")


def process_file(src: str, options: Options, index: int = 0, used_names: Optional[set] = None,
                 log: Optional[Callable[[str], None]] = None) -> ProcessResult:
    """处理单张照片并落盘。"""
    res = ProcessResult(src)
    t0 = time.time()
    try:
        res.in_bytes = os.path.getsize(src)
        res.meta_before = read_metadata_summary(src)

        out_dir = default_output_dir(src, options)
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        if used_names is None:
            used_names = set()
        name = _target_name(index, src, options, used_names)
        used_names.add(name.lower())
        out_path = os.path.join(out_dir, name)

        im = _open_image(src)
        mdeg = 0
        try:
            mdeg = int((options.rotate_map or {}).get(os.path.abspath(src), 0) or 0)
        except Exception:
            mdeg = 0
        im_out, stats = desensitize(im, options, log, manual_deg=mdeg)

        fmt, _ext = resolve_format(src, options)
        _save(im_out, out_path, options, fmt)
        res.out = out_path
        res.faces = stats["faces"]
        res.plates = stats["plates"]
        res.texts = stats["texts"]
        res.rotated = stats.get("rotated", 0)
        res.out_bytes = os.path.getsize(out_path)
        res.meta_after = read_metadata_summary(out_path)
        res.ok = True
    except Exception as e:
        res.error = "%s: %s" % (type(e).__name__, e)
    res.elapsed = time.time() - t0
    return res


def process_batch(paths: List[str], options: Options,
                  progress: Optional[Callable[[int, int, ProcessResult], None]] = None,
                  should_stop: Optional[Callable[[], bool]] = None,
                  log: Optional[Callable[[str], None]] = None) -> Dict[str, object]:
    """批量处理入口（供 CLI / HTTP API / 其他程序调用）。

    progress: 回调 (已完成数量, 总数, 单张结果)
    should_stop: 返回 True 时提前结束（用于界面「停止」按钮）
    """
    results = []
    total = len(paths)
    used = set()
    for i, p in enumerate(paths):
        if should_stop and should_stop():
            break
        r = process_file(p, options, index=i, used_names=used, log=log)
        results.append(r)
        if progress:
            progress(i + 1, total, r)
    ok = [r for r in results if r.ok]
    fail = [r for r in results if not r.ok]
    return {
        "total": total,
        "done": len(results),
        "ok": len(ok),
        "failed": len(fail),
        "faces": sum(r.faces for r in ok),
        "plates": sum(r.plates for r in ok),
        "texts": sum(r.texts for r in ok),
        "rotated_imgs": sum(1 for r in ok if getattr(r, "rotated", 0)),
        "results": [r.to_dict() for r in results],
    }


def list_images(folder: str) -> List[str]:
    """递归收集文件夹内的图片（按名称排序）。"""
    found = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() in SUPPORTED_EXT:
                found.append(os.path.join(root, f))
    return sorted(found)
