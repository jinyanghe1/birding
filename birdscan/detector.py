"""L1 检测层：判断「图里有没有动物」。

主后端：MegaDetector V6（ultralytics 直载 .pt，绕过 PytorchWildlife 的
timm/yolov5/soundfile 重依赖链）。

本机实测 benchmark（32 张真实照片，Apple Silicon）：
    mps  imgsz=640  ->   23 ms/张
    mps  imgsz=960  ->   44 ms/张   <- 采用（全库 15,413 张约 11 分钟）
    cpu  imgsz=640  ->  188 ms/张
    cpu  imgsz=960  ->  389 ms/张
MPS 比 CPU 快约 8 倍。

降级后端：Apple Vision（pyobjc），零下载，但只有 bird/owl 等粗粒度标签。

注意：MegaDetector 官方已下架性能数字（承认验证集可能损坏），
网上的 "82.8% recall" 是第三方镜像数字，不可引用。
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

from . import config

log = logging.getLogger("birdscan")

_local = threading.local()


@dataclass
class Detection:
    has_animal: bool
    max_conf: float
    n_boxes: int
    backend: str


class Detector:
    def __init__(self, conf: float | None = None, weights: str | None = None,
                 imgsz: int | None = None, device: str | None = None):
        self.conf = conf if conf is not None else config.DETECT_CONF
        self.imgsz = imgsz or config.DETECT_INPUT_SIZE
        self.weights = weights or config.MD_WEIGHTS
        self.device = device or config.MD_DEVICE
        self.backend = "none"
        self._model = None
        self._lock = threading.Lock()
        self._init_backend()

    # ------------------------------------------------------------ 后端
    def _init_backend(self) -> None:
        path = os.path.join(config.MODEL_DIR, self.weights)
        if os.path.exists(path):
            try:
                from ultralytics import YOLO
                self._model = YOLO(path)
                self.backend = "megadetector"
                log.info("L1 = MegaDetector %s (device=%s, imgsz=%d)",
                         self.weights, self.device, self.imgsz)
                return
            except Exception as e:
                log.warning("MegaDetector 加载失败（%s）", e)
        else:
            log.warning("权重不存在：%s", path)
        if self._init_vision():
            self.backend = "apple_vision"
            log.info("L1 = Apple Vision（降级模式）")
            return
        self.backend = "none"
        log.error("无可用检测器，L1 将放行全部图片")

    def _init_vision(self) -> bool:
        try:
            import Vision  # noqa: F401
            return True
        except ImportError:
            return False

    # ------------------------------------------------------------ 推理
    def detect_batch(self, paths: list[str]) -> list[Detection]:
        if not paths:
            return []
        if self.backend == "megadetector":
            return self._md(paths)
        if self.backend == "apple_vision":
            return [self._vision(p) for p in paths]
        return [Detection(True, 0.0, 0, "none") for _ in paths]

    def _md(self, paths: list[str]) -> list[Detection]:
        out = [Detection(False, 0.0, 0, "megadetector") for _ in paths]
        try:
            # ultralytics 非线程安全，串行化；MPS 也需要串行提交
            with self._lock:
                results = self._model.predict(
                    paths, imgsz=self.imgsz, conf=self.conf,
                    device=self.device, verbose=False,
                )
        except Exception as e:
            log.warning("批量推理失败（%s），逐张重试", type(e).__name__)
            for i, p in enumerate(paths):
                try:
                    with self._lock:
                        r = self._model.predict([p], imgsz=self.imgsz, conf=self.conf,
                                                device=self.device, verbose=False)
                    out[i] = self._parse(r[0] if r else None)
                except Exception as e2:
                    log.debug("单张失败 %s: %s", p, e2)
            return out
        for i, r in enumerate(results or []):
            if i < len(out):
                out[i] = self._parse(r)
        return out

    def _parse(self, r) -> Detection:
        if r is None or not hasattr(r, "boxes") or r.boxes is None:
            return Detection(False, 0.0, 0, "megadetector")
        best, n = 0.0, 0
        for b in r.boxes:
            if int(b.cls) != 0:      # 0 = animal（1=person, 2=vehicle）
                continue
            n += 1
            best = max(best, float(b.conf))
        return Detection(n > 0, best, n, "megadetector")

    def _vision(self, path: str) -> Detection:
        """Apple Vision：能识别 bird/heron/owl/gull，但没有鸟种。"""
        try:
            import Vision
            from Foundation import NSURL
            handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
                NSURL.fileURLWithPath_(path), None
            )
            request = Vision.VNClassifyImageRequest.alloc().init()
            handler.performRequests_error_([request], None)
            best = 0.0
            for obs in (request.results() or []):
                ident = str(obs.identifier()).lower()
                if any(k in ident for k in config.VISION_BIRD_LABELS):
                    best = max(best, float(obs.confidence()))
            return Detection(best >= config.VISION_CONF, best,
                             1 if best else 0, "apple_vision")
        except Exception as e:
            log.debug("Vision 失败 %s: %s", path, e)
            return Detection(True, 0.0, 0, "apple_vision")
