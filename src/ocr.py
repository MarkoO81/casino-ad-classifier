"""OCR for extracting in-image text from banner creatives.

Most casino banner ads put their key copy ("100 FREE SPINS", "UP TO €500
WELCOME BONUS") IN the image, not in the ad caption. OCR catches what
keyword-matching the ad copy would miss.

Default backend: PaddleOCR (handles SL diacritics well, supports many langs).
Alternative: EasyOCR (simpler install, slightly less accurate on stylized fonts).

Both are heavy dependencies. The OCR step is the slowest part of the
per-creative pipeline; budget for ~200-500ms per image on CPU, ~50ms on GPU.
"""

from typing import Union
from pathlib import Path


class OCREngine:
    """Pluggable OCR — defaults to PaddleOCR if available."""

    def __init__(self, backend: str = "paddle", langs: list = None):
        self.backend = backend
        self.langs = langs or ["en", "sl", "hr"]
        self._engine = None

    def _ensure_loaded(self):
        if self._engine is not None:
            return
        if self.backend == "paddle":
            from paddleocr import PaddleOCR
            # Paddle uses 'en' or 'latin' for European; 'latin' covers SL/HR/SR
            self._engine = PaddleOCR(use_angle_cls=True, lang="latin",
                                     show_log=False)
        elif self.backend == "easyocr":
            import easyocr
            # EasyOCR doesn't ship a Slovenian model; 'hr' covers diacritics
            langs = [l for l in self.langs if l != "sl"] + ["hr"]
            self._engine = easyocr.Reader(list(set(langs)), gpu=False)
        else:
            raise ValueError(f"Unknown OCR backend: {self.backend}")

    def extract(self, image: Union[str, Path]) -> str:
        """Return all extracted text concatenated, lowercased."""
        self._ensure_loaded()
        path = str(image)
        if self.backend == "paddle":
            result = self._engine.ocr(path, cls=True)
            if not result or not result[0]:
                return ""
            lines = [line[1][0] for line in result[0] if line and line[1]]
            return " ".join(lines).lower()
        else:  # easyocr
            result = self._engine.readtext(path, detail=0)
            return " ".join(result).lower()
