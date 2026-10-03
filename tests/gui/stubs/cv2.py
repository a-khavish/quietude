# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal cv2 stub: lets us exercise Maya's own logic without the real
wheel. Mirrors only what face_auth.py touches."""
import numpy as np
IMREAD_COLOR = 1
IMREAD_GRAYSCALE = 0
COLOR_BGR2GRAY = 6
class _Data: haarcascades = "/nonexistent/"
data = _Data()
class CascadeClassifier:
    def __init__(self, path): self._path = path
    def empty(self): return False
    def detectMultiScale(self, gray, **kw): return [(10, 10, 100, 100)]
def imdecode(arr, flag): return np.zeros((240, 320, 3), dtype=np.uint8)
def cvtColor(img, code): return np.zeros(img.shape[:2], dtype=np.uint8)
def resize(img, size): return np.zeros((size[1], size[0]), dtype=np.uint8)
def equalizeHist(img): return img
def imwrite(path, img):
    open(path, "wb").write(b"\x89PNG stub"); return True
def imread(path, flag=1): return np.zeros((200, 200), dtype=np.uint8)
class _LBPH:
    def train(self, images, labels): self._n = len(images)
    def save(self, path): open(path, "w").write("stub-model")
    def read(self, path): pass
    def predict(self, face): return (0, 12.0)
class _Face:
    @staticmethod
    def LBPHFaceRecognizer_create(): return _LBPH()
face = _Face()
