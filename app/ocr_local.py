# -*- coding: utf-8 -*-
"""
本地 OCR（PP-OCRv4 轻量版，ONNX Runtime 推理，完全离线）
=========================================================
用途：识别照片里的证件号 / 手机号 / 银行卡号，只对这些数字打码。
模型文件放在 app/assets/models/ 下，缺失时会尝试从国内镜像（ModelScope）下载。
"""
from __future__ import annotations

import os
import threading
from typing import List, Optional, Tuple

import numpy as np

import cv2

_MODEL_FILES = {
    "det": "ch_PP-OCRv4_det_mobile.onnx",
    "rec": "ch_PP-OCRv4_rec_mobile.onnx",
    "dict": "ppocr_keys_v1.txt",
}

_MIRROR_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/master"
_MIRROR_PATHS = {
    "det": _MIRROR_BASE + "/onnx/PP-OCRv4/det/ch_PP-OCRv4_det_mobile.onnx",
    "rec": _MIRROR_BASE + "/onnx/PP-OCRv4/rec/ch_PP-OCRv4_rec_mobile.onnx",
    "dict": _MIRROR_BASE + "/paddle/PP-OCRv4/rec/ch_PP-OCRv4_rec_mobile/ppocr_keys_v1.txt",
}


def model_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "models")


def models_ready() -> bool:
    d = model_dir()
    return all(os.path.exists(os.path.join(d, f)) for f in _MODEL_FILES.values())


def download_models(log=None) -> bool:
    """从国内镜像下载 OCR 模型（约 15MB）。返回是否成功。"""
    import urllib.request
    d = model_dir()
    os.makedirs(d, exist_ok=True)
    for key, fname in _MODEL_FILES.items():
        dst = os.path.join(d, fname)
        if os.path.exists(dst) and os.path.getsize(dst) > 1024:
            continue
        tmp = dst + ".part"
        if log:
            log("正在下载 OCR 模型 %s ..." % fname)
        try:
            with urllib.request.urlopen(_MIRROR_PATHS[key], timeout=60) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, dst)
        except Exception as e:
            if log:
                log("下载失败：%s" % e)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            return False
    return True


class LocalOCR(object):
    """极简 OCR：DB 文本检测 + CTC 识别，够用于定位敏感数字。"""

    def __init__(self, folder: Optional[str] = None):
        self.folder = folder or model_dir()
        self.err = ""
        self._lock = threading.Lock()
        self._det = None
        self._rec = None
        self._chars: List[str] = []
        self._load_error = ""

    # ---------------------------------------------------------------- 状态
    def available(self) -> bool:
        if not models_ready():
            self.err = "OCR 模型未安装（可在设置里一键下载）"
            return False
        try:
            import onnxruntime  # noqa: F401
        except Exception as e:
            self.err = "缺少 onnxruntime：%s" % e
            return False
        return True

    def status_text(self) -> str:
        return "本地 OCR 就绪" if self.available() and not self._load_error else (self._load_error or self.err or "本地 OCR 未就绪")

    # ---------------------------------------------------------------- 加载
    def _load(self) -> None:
        if self._det is not None:
            return
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._det = ort.InferenceSession(os.path.join(self.folder, _MODEL_FILES["det"]), opts, providers=["CPUExecutionProvider"])
        self._rec = ort.InferenceSession(os.path.join(self.folder, _MODEL_FILES["rec"]), opts, providers=["CPUExecutionProvider"])
        dict_path = os.path.join(self.folder, _MODEL_FILES["dict"])
        with open(dict_path, "r", encoding="utf-8") as f:
            self._chars = [line.rstrip("\n").rstrip("\r") for line in f if line.strip("\n") != ""]

    # ---------------------------------------------------------------- 检测
    def _detect_boxes(self, bgr: np.ndarray) -> Tuple[List[np.ndarray], float]:
        h, w = bgr.shape[:2]
        scale = 1.0
        # 1440 比 960 能多检出近一半的小字（证件上的姓名、地址常是小字号）
        if max(h, w) > 1440:
            scale = 1440.0 / max(h, w)
        img = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr
        # 尺寸对齐到 32 的倍数
        hh = int(round(img.shape[0] / 32.0) * 32)
        ww = int(round(img.shape[1] / 32.0) * 32)
        hh = max(32, hh)
        ww = max(32, ww)
        img = cv2.resize(img, (ww, hh))
        x = img[:, :, ::-1].astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        x = (x - mean) / std
        x = np.transpose(x, (2, 0, 1))[None, ...]
        name = self._det.get_inputs()[0].name
        pred = self._det.run(None, {name: x})[0]
        prob = pred[0, 0]
        bitmap = (prob > 0.35).astype(np.uint8)
        # 同一行文字常有断裂，横向闭运算先把它们连成整行
        bitmap = cv2.morphologyEx(bitmap, cv2.MORPH_CLOSE,
                                  cv2.getStructuringElement(cv2.MORPH_RECT, (13, 3)))
        contours, _ = cv2.findContours(bitmap, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for c in contours:
            x0, y0, bw, bh = cv2.boundingRect(c)
            if bw < 8 or bh < 8:
                continue
            if bw * bh < 80:
                continue
            pm = prob[y0:y0 + bh, x0:x0 + bw]
            # 细长文字行的外框大面积是空白，平均概率会被拉低；
            # 用 90 分位数兜底，避免整行地址/姓名被误滤掉
            if pm.mean() < 0.45 and float(np.percentile(pm, 90)) < 0.60:
                continue
            # DB 模型输出的是「文字内核」，按 unclip 规则外扩回完整文字框
            area = float(bw * bh)
            perimeter = 2.0 * float(bw + bh)
            dist = area * 1.8 / max(perimeter, 1.0)
            nx1 = max(0, x0 - int(dist))
            ny1 = max(0, y0 - int(dist))
            nx2 = min(ww, x0 + bw + int(dist))
            ny2 = min(hh, y0 + bh + int(dist))
            boxes.append(np.array([nx1, ny1, nx2, ny2], dtype=np.float32))
        return boxes, (float(ww) / float(w), float(hh) / float(h))

    # ---------------------------------------------------------------- 识别
    @staticmethod
    def _tighten(crop: np.ndarray) -> np.ndarray:
        """去掉文字行四周的空白，让小字在 48px 输入里占满高度。"""
        try:
            if crop.size == 0 or crop.shape[0] < 8 or crop.shape[1] < 8:
                return crop
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            _t, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            rows = np.where(bw.sum(axis=1) > 0)[0]
            cols = np.where(bw.sum(axis=0) > 0)[0]
            if len(rows) < 4 or len(cols) < 4:
                return crop
            y1, y2 = int(rows[0]), int(rows[-1]) + 1
            x1, x2 = int(cols[0]), int(cols[-1]) + 1
            if (y2 - y1) < crop.shape[0] * 0.35 or (x2 - x1) < crop.shape[1] * 0.35:
                return crop      # 裁得太狠，可能是噪点，放弃
            py = max(2, int((y2 - y1) * 0.06))
            px = max(2, int((x2 - x1) * 0.03))
            h, w = crop.shape[:2]
            return crop[max(0, y1 - py):min(h, y2 + py), max(0, x1 - px):min(w, x2 + px)]
        except Exception:
            return crop

    def _recognize_crop(self, crop: np.ndarray) -> Tuple[str, float]:
        if crop.size == 0:
            return "", 0.0
        h, w = crop.shape[:2]
        target_h = 48
        target_w = int(round(w / float(h) * target_h)) if h > 0 else target_h
        target_w = max(16, min(960, target_w))
        img = cv2.resize(crop, (target_w, target_h))
        x = img[:, :, ::-1].astype(np.float32) / 255.0
        x = (x - 0.5) / 0.5
        x = np.transpose(x, (2, 0, 1))[None, ...]
        name = self._rec.get_inputs()[0].name
        out = self._rec.run(None, {name: x})[0][0]   # [T, C]
        idx = out.argmax(axis=1)
        scores = out.max(axis=1)
        n_cls = out.shape[1]
        chars = self._chars
        text = []
        conf = []
        prev = -1
        for t, i in enumerate(idx):
            if i == prev:
                continue
            prev = i
            if i == 0:
                continue
            if n_cls == len(chars) + 2:          # [blank] + chars + [space]
                if i == len(chars) + 1:
                    text.append(" ")
                elif 1 <= i <= len(chars):
                    text.append(chars[i - 1])
            elif n_cls == len(chars) + 1:        # chars + [blank]
                if 1 <= i <= len(chars):
                    text.append(chars[i - 1])
            else:
                if 1 <= i < len(chars):
                    text.append(chars[i - 1])
            conf.append(float(scores[t]))
        if not text:
            return "", 0.0
        return "".join(text), float(sum(conf) / len(conf))

    def recognize_crop(self, crop: np.ndarray) -> Tuple[str, float]:
        """识别一小块区域（供车牌校验等外部逻辑复用）。"""
        if not self.available():
            return "", 0.0
        with self._lock:
            try:
                self._load()
            except Exception as e:
                self._load_error = "OCR 模型加载失败：%s" % e
                return "", 0.0
            try:
                return self._recognize_crop(crop)
            except Exception:
                return "", 0.0

    def recognize(self, bgr: np.ndarray) -> List[Tuple[Tuple[int, int, int, int], str, float]]:
        """返回 [(原图坐标框, 文本, 置信度)]。"""
        results: List[Tuple[Tuple[int, int, int, int], str, float]] = []
        if not self.available():
            return results
        with self._lock:
            try:
                self._load()
            except Exception as e:
                self._load_error = "OCR 模型加载失败：%s" % e
                return results
            try:
                boxes, (rx, ry) = self._detect_boxes(bgr)
            except Exception as e:
                self._load_error = "OCR 检测失败：%s" % e
                return results
            H, W = bgr.shape[:2]
            for b in boxes:
                x1, y1, x2, y2 = [int(v) for v in b]
                # 映射回原图，并稍微外扩，保证号码首尾不被切掉
                pad_x = int((x2 - x1) * 0.03) + 2
                pad_y = int((y2 - y1) * 0.06) + 2
                ox1 = int((x1 - pad_x) / rx); oy1 = int((y1 - pad_y) / ry)
                ox2 = int((x2 + pad_x) / rx); oy2 = int((y2 + pad_y) / ry)
                ox1 = max(0, min(W - 1, ox1)); oy1 = max(0, min(H - 1, oy1))
                ox2 = max(ox1 + 2, min(W, ox2)); oy2 = max(oy1 + 2, min(H, oy2))
                crop = bgr[oy1:oy2, ox1:ox2]
                if crop.size == 0:
                    continue
                crop = self._tighten(crop)
                text, conf = self._recognize_crop(crop)
                if text and conf > 0.50:
                    results.append(((ox1, oy1, ox2, oy2), text, conf))
        return results
