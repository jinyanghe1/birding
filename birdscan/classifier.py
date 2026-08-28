"""L3a 本地鸟种分类（可选）。

使用 Hakureirm/bird-id-models 的 ConvNeXt ONNX：
  输入 [N,3,224,224]，输出 [N,10753]，labels_cn_10753.txt 提供中文名。

⚠️ 重要风险提示（实测核实，非道听途说）：
  * 该仓库 0 star、0 fork、HF 模型 0 下载、**仓库内没有 LICENSE 文件**
    （只有 PyPI 元数据声明 MIT，法律上存疑）；
  * 作者只给了速度自测（x86 ~600ms/张），**没有任何精度 benchmark**；
  * 中文名来源未标注。
因此本模块定位是「候选建议」，结果一律标记 identified_by='model'，
置信度低于阈值的转人工复核，不直接当作最终结论。
默认关闭，需在 config 里显式开启。
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from . import config

log = logging.getLogger("birdscan")

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class BirdClassifier:
    def __init__(self, weights: str | None = None, size: int = 224):
        self.size = size
        self.name = weights or "convnext_bird_cls.onnx"
        self.path = Path(config.MODEL_DIR) / self.name
        self._sess = None
        self._cn: list[str] = []
        self._en: list[str] = []
        self._load()

    def _load(self) -> None:
        import onnxruntime as ort
        self._sess = ort.InferenceSession(
            str(self.path), providers=["CPUExecutionProvider"]
        )
        cn_f = Path(config.MODEL_DIR) / "labels_cn_10753.txt"
        en_f = Path(config.MODEL_DIR) / "labels_10753.txt"
        if cn_f.exists():
            for line in cn_f.read_text(encoding="utf-8").splitlines():
                parts = line.rstrip("\n").split("\t")
                self._cn.append(parts[1] if len(parts) > 1 else parts[0])
        if en_f.exists():
            for line in en_f.read_text(encoding="utf-8").splitlines():
                self._en.append(line.rstrip("\n").split("\t")[0])
        log.info("分类器载入：%s，%d 类", self.name, len(self._cn))

    def preprocess(self, img) -> np.ndarray:
        """img: PIL.Image -> [1,3,224,224] float32（ImageNet 归一化）。"""
        from PIL import Image
        im = img.convert("RGB")
        # 保持长宽比缩放后中心裁剪，避免形变
        s = self.size
        w, h = im.size
        scale = s / min(w, h)
        im = im.resize((max(s, int(round(w * scale))), max(s, int(round(h * scale)))))
        w, h = im.size
        left, top = (w - s) // 2, (h - s) // 2
        im = im.crop((left, top, left + s, top + s))
        a = np.asarray(im, dtype=np.float32) / 255.0
        a = (a - _MEAN) / _STD
        return np.transpose(a, (2, 0, 1))[None, ...]

    def classify_pil(self, img, topk: int = 3) -> list[dict]:
        x = self.preprocess(img)
        out = self._sess.run(None, {self._sess.get_inputs()[0].name: x})[0]
        probs = _softmax(out[0])
        idx = np.argsort(-probs)[:topk]
        res = []
        for i in idx:
            res.append({
                "common_name_cn": self._cn[i] if i < len(self._cn) else str(i),
                "common_name_en": self._en[i] if i < len(self._en) else None,
                "confidence": float(probs[i]),
            })
        return res

    def predict(self, path: str, topk: int = 3) -> list[dict]:
        from PIL import Image
        try:
            im = Image.open(path)
        except Exception as e:
            log.debug("打开失败 %s: %s", path, e)
            return []
        return self.classify_pil(im, topk)

    def predict_batch(self, paths: list[str], topk: int = 3) -> list[list[dict]]:
        return [self.predict(p, topk) for p in paths]


def identify_file(path: str, topk: int = 2) -> dict:
    """完整识别：YOLO 先框鸟 -> 裁剪 -> ConvNeXt 分类。

    返回 {"is_bird": bool, "box_conf": float, "candidates": [...]}
    """
    from PIL import Image
    result = {"is_bird": False, "box_conf": 0.0, "candidates": []}
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return result
    try:
        det = _get_detector()
        boxes = det.detect(im)
    except Exception as e:
        log.debug("鸟检测失败 %s: %s", path, e)
        return result
    if not boxes:
        return result
    x1, y1, x2, y2, bconf = boxes[0]
    result["box_conf"] = bconf
    if (x2 - x1) < 8 or (y2 - y1) < 8:
        return result
    crop = im.crop((x1, y1, x2, y2))
    clf = _get_classifier()
    cands = clf.classify_pil(crop, topk)
    result["candidates"] = cands
    result["is_bird"] = bool(cands)
    return result


_DET = None
_CLF = None


def _get_detector():
    global _DET
    if _DET is None:
        from .birddet import BirdDetector
        _DET = BirdDetector()
    return _DET


def _get_classifier():
    global _CLF
    if _CLF is None:
        _CLF = BirdClassifier()
    return _CLF


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - np.max(x)
    e = np.exp(x)
    return e / np.sum(e)
