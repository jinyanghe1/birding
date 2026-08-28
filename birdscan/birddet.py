"""YOLO 鸟类检测器：先框出鸟，再交给分类器，避免整图直推时鸟太小导致误判。

模型来自 Hakureirm/bird-id-models 的 yolo_bird_detect.onnx
  输入 [1,3,640,640]，输出 [1,5,8400]（cx,cy,w,h,conf）
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image

from . import config

log = logging.getLogger("birdscan")


class BirdDetector:
    def __init__(self, weights: str = "yolo_bird_detect.onnx", size: int = 640):
        self.size = size
        self.path = Path(config.MODEL_DIR) / weights
        import onnxruntime as ort
        self._s = ort.InferenceSession(str(self.path), providers=["CPUExecutionProvider"])

    def _letterbox(self, im):
        w, h = im.size
        ratio = min(self.size / w, self.size / h)
        nw, nh = int(round(w * ratio)), int(round(h * ratio))
        canvas = im.resize((nw, nh))
        bg = Image.new("RGB", (self.size, self.size), (114, 114, 114))
        pad_x, pad_y = (self.size - nw) // 2, (self.size - nh) // 2
        bg.paste(canvas, (pad_x, pad_y))
        return bg, ratio, pad_x, pad_y

    def detect(self, im, conf: float = 0.25, max_out: int = 5) -> list[tuple[int, int, int, int, float]]:
        """返回 [(x1,y1,x2,y2,score)]，坐标为原图像素。"""
        lb, ratio, pad_x, pad_y = self._letterbox(im)
        x = np.asarray(lb, dtype=np.float32) / 255.0
        x = np.transpose(x, (2, 0, 1))[None, ...]
        out = self._s.run(None, {self._s.get_inputs()[0].name: x})[0][0]   # [5, 8400]
        o = out.T
        keep = o[:, 4] > conf
        o = o[keep]
        if len(o) == 0:
            return []
        cx, cy, bw, bh, sc = o[:, 0], o[:, 1], o[:, 2], o[:, 3], o[:, 4]
        x1 = (cx - bw / 2 - pad_x) / ratio
        y1 = (cy - bh / 2 - pad_y) / ratio
        x2 = (cx + bw / 2 - pad_x) / ratio
        y2 = (cy + bh / 2 - pad_y) / ratio
        order = np.argsort(-sc)[:max_out]
        W, H = im.size
        res = []
        for i in order:
            res.append((max(0, int(x1[i])), max(0, int(y1[i])),
                        min(W, int(x2[i])), min(H, int(y2[i])), float(sc[i])))
        return res
