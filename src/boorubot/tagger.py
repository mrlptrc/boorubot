"""Sugestão de tags e rating com o WD Tagger v3 (SmilingWolf), rodando local.

O modelo é treinado no vocabulário do Danbooru, então as tags já saem no formato dos boorus.
Primeiro uso baixa ~380 MB do Hugging Face para o cache local.
Pré-processamento copiado do Space oficial (SmilingWolf/wd-tagger, app.py).
"""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

MODEL_REPO = "SmilingWolf/wd-vit-tagger-v3"
GENERAL_THRESHOLD = 0.35
CHARACTER_THRESHOLD = 0.85
RATING_CATEGORY, GENERAL_CATEGORY, CHARACTER_CATEGORY = "9", "0", "4"


@dataclass
class Suggestion:
    rating: str
    rating_scores: dict[str, float]
    general: list[tuple[str, float]]
    characters: list[tuple[str, float]]

    @property
    def tags(self) -> list[str]:
        return [t for t, _ in self.characters] + [t for t, _ in self.general]


class Tagger:
    def __init__(self, repo: str = MODEL_REPO):
        self.repo = repo
        self._session = None
        self._labels: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def _load(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download

        model_path = hf_hub_download(self.repo, "model.onnx")
        tags_path = hf_hub_download(self.repo, "selected_tags.csv")
        with open(tags_path, encoding="utf-8") as fh:
            self._labels = [(row["name"], row["category"]) for row in csv.DictReader(fh)]
        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])

    def _prepare(self, path: Path) -> np.ndarray:
        size = self._session.get_inputs()[0].shape[1]
        image = Image.open(path).convert("RGBA")
        canvas = Image.new("RGBA", image.size, (255, 255, 255))
        canvas.alpha_composite(image)
        image = canvas.convert("RGB")
        side = max(image.size)
        padded = Image.new("RGB", (side, side), (255, 255, 255))
        padded.paste(image, ((side - image.size[0]) // 2, (side - image.size[1]) // 2))
        if side != size:
            padded = padded.resize((size, size), Image.BICUBIC)
        array = np.asarray(padded, dtype=np.float32)[:, :, ::-1]  # RGB -> BGR
        return np.expand_dims(array, axis=0)

    def suggest(self, path: Path) -> Suggestion:
        with self._lock:
            self._load()
            inputs = {self._session.get_inputs()[0].name: self._prepare(path)}
            scores = self._session.run(None, inputs)[0][0].astype(float)

        ratings, general, characters = {}, [], []
        for (name, category), score in zip(self._labels, scores):
            if category == RATING_CATEGORY:
                ratings[name] = score
            elif category == CHARACTER_CATEGORY and score > CHARACTER_THRESHOLD:
                characters.append((name, score))
            elif category == GENERAL_CATEGORY and score > GENERAL_THRESHOLD:
                general.append((name, score))
        return Suggestion(
            rating=max(ratings, key=ratings.get),
            rating_scores=ratings,
            general=sorted(general, key=lambda x: -x[1]),
            characters=sorted(characters, key=lambda x: -x[1]),
        )


tagger = Tagger()
