"""
PixAI Tagger Module

Provides image tagging functionality using PixAI Tagger v0.9 model.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict

import torch
import torchvision.transforms as transforms
from PIL import Image
from huggingface_hub import hf_hub_download
import timm


class TaggingHead(torch.nn.Module):
    """Classification head for tagging model"""

    def __init__(self, input_dim, num_classes):
        super().__init__()
        self.input_dim = input_dim
        self.num_classes = num_classes
        self.head = torch.nn.Sequential(torch.nn.Linear(input_dim, num_classes))

    def forward(self, x):
        logits = self.head(x)
        probs = torch.nn.functional.sigmoid(logits)
        return probs


def get_tags(tags_file: Path) -> tuple[dict[str, int], int, int]:
    """Load tag mapping from JSON file"""
    with tags_file.open("r", encoding="utf-8") as f:
        tag_info = json.load(f)
    tag_map = tag_info["tag_map"]
    tag_split = tag_info["tag_split"]
    gen_tag_count = tag_split["gen_tag_count"]
    character_tag_count = tag_split["character_tag_count"]
    return tag_map, gen_tag_count, character_tag_count


def get_character_ip_mapping(mapping_file: Path) -> Dict[str, list]:
    """Load character to IP mapping from JSON file"""
    with mapping_file.open("r", encoding="utf-8") as f:
        mapping = json.load(f)
    return mapping


def get_encoder():
    """Get the encoder model (EVA02)"""
    base_model_repo = "hf_hub:SmilingWolf/wd-eva02-large-tagger-v3"
    encoder = timm.create_model(base_model_repo, pretrained=False)
    encoder.reset_classifier(0)
    return encoder


def get_decoder():
    """Get the decoder head"""
    decoder = TaggingHead(1024, 13461)
    return decoder


def get_model():
    """Get the complete model (encoder + decoder)"""
    encoder = get_encoder()
    decoder = get_decoder()
    model = torch.nn.Sequential(encoder, decoder)
    return model


def load_model(weights_file, device):
    """Load model weights from file"""
    model = get_model()
    states_dict = torch.load(weights_file, map_location=device, weights_only=True)
    model.load_state_dict(states_dict)
    model.to(device)
    model.eval()
    return model


def pure_pil_alpha_to_color_v2(
    image: Image.Image, color: tuple[int, int, int] = (255, 255, 255)
) -> Image.Image:
    """Convert a PIL image with an alpha channel to a RGB image"""
    image.load()  # needed for split()
    background = Image.new("RGB", image.size, color)
    background.paste(image, mask=image.split()[3])  # 3 is the alpha channel
    return background


def pil_to_rgb(image: Image.Image) -> Image.Image:
    """Convert image to RGB mode"""
    if image.mode == "RGBA":
        image = pure_pil_alpha_to_color_v2(image)
    elif image.mode == "P":
        image = pure_pil_alpha_to_color_v2(image.convert("RGBA"))
    else:
        image = image.convert("RGB")
    return image


class PixAITagger:
    """PixAI Tagger for image tagging"""

    # Default thresholds based on requirements
    DEFAULT_FEATURE_THRESHOLDS = {
        'high': 0.35,
        'medium': 0.25,
        'low': 0.1,
    }

    DEFAULT_CHARACTER_THRESHOLDS = {
        'high': 0.9,
        'medium': 0.8,
        'low': 0.5,
    }

    # Thresholds for saving to raw_scores (lower than display thresholds)
    RAW_SCORE_FEATURE_THRESHOLD = 0.05
    RAW_SCORE_CHARACTER_THRESHOLD = 0.2

    def __init__(self, model_dir: Path, device: str = None):
        """
        Initialize PixAI Tagger

        Args:
            model_dir: Directory containing model files
            device: Device to use ('cuda' or 'cpu'). Auto-detect if None.
        """
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

        # Download required files if not present
        self._download_model_files()

        weights_file = self.model_dir / "model_v0.9.pth"
        tags_file = self.model_dir / "tags_v0.9_13k.json"
        mapping_file = self.model_dir / "char_ip_map.json"

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        print(f"[PixAI] Using device: {self.device}")

        print("[PixAI] Loading model...")
        self.model = load_model(str(weights_file), self.device)

        self.transform = transforms.Compose(
            [
                transforms.Resize((448, 448)),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
            ]
        )

        tag_map, self.gen_tag_count, self.character_tag_count = get_tags(tags_file)

        # Invert the tag_map for efficient index-to-tag lookups
        self.index_to_tag_map = {v: k for k, v in tag_map.items()}

        self.character_ip_mapping = get_character_ip_mapping(mapping_file)

        print(f"[PixAI] Model loaded. Total tags: {len(tag_map)}")
        print(f"[PixAI]   - General tags: {self.gen_tag_count}")
        print(f"[PixAI]   - Character tags: {self.character_tag_count}")

    def _download_model_files(self):
        """Download required model files from Hugging Face"""
        repo_id = "pixai-labs/pixai-tagger-v0.9"
        files = [
            "model_v0.9.pth",
            "tags_v0.9_13k.json",
            "char_ip_map.json",
        ]

        for filename in files:
            local_path = self.model_dir / filename
            if not local_path.exists():
                print(f"[PixAI] Downloading {filename}...")
                hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    local_dir=self.model_dir,
                )

    def tag_image(
        self,
        image: Image.Image,
        feature_thresholds: dict[str, float] = None,
        character_thresholds: dict[str, float] = None,
    ) -> dict[str, Any]:
        """
        Tag an image with PixAI tagger

        Args:
            image: PIL Image
            feature_thresholds: Dict with 'high', 'medium', 'low' thresholds for features
            character_thresholds: Dict with 'high', 'medium', 'low' thresholds for characters

        Returns:
            Dictionary containing tag_list and raw_scores
        """
        # Use default thresholds if not specified
        if feature_thresholds is None:
            feature_thresholds = self.DEFAULT_FEATURE_THRESHOLDS

        if character_thresholds is None:
            character_thresholds = self.DEFAULT_CHARACTER_THRESHOLDS

        # Preprocess image
        image = pil_to_rgb(image)

        start_time = time.time()

        with torch.inference_mode():
            # Preprocess image on CPU, then pin memory for faster async transfer
            image_tensor = self.transform(image).unsqueeze(0).pin_memory()

            # Asynchronously move image to GPU
            image_tensor = image_tensor.to(self.device, non_blocking=True)

            # Run model on GPU
            probs = self.model(image_tensor)[0]  # Get probs for the single image

            # Move to CPU for processing
            probs_cpu = probs.cpu()

        inference_time = time.time() - start_time

        # Extract general (feature) tags
        general_probs = probs_cpu[: self.gen_tag_count]
        # Extract character tags
        character_probs = probs_cpu[self.gen_tag_count :]

        # Build results
        tag_list = {
            'high_confidence': {'character': {}, 'feature': {}, 'ip': {}},
            'medium_confidence': {'character': {}, 'feature': {}, 'ip': {}},
            'low_confidence': {'character': {}, 'feature': {}, 'ip': {}},
        }

        raw_scores = {
            'character': {},
            'feature': {},
        }

        # Process feature tags
        for idx, prob in enumerate(general_probs):
            score = prob.item()
            tag = self.index_to_tag_map[idx]

            # Save to raw_scores if above the raw score threshold
            if score >= self.RAW_SCORE_FEATURE_THRESHOLD:
                raw_scores['feature'][tag] = score

            # Add to tag_list based on confidence thresholds
            if score >= feature_thresholds['high']:
                tag_list['high_confidence']['feature'][tag] = True
            if score >= feature_thresholds['medium']:
                tag_list['medium_confidence']['feature'][tag] = True
            if score >= feature_thresholds['low']:
                tag_list['low_confidence']['feature'][tag] = True

        # Process character tags
        for idx, prob in enumerate(character_probs):
            score = prob.item()
            tag = self.index_to_tag_map[idx + self.gen_tag_count]

            # Save to raw_scores if above the raw score threshold
            if score >= self.RAW_SCORE_CHARACTER_THRESHOLD:
                raw_scores['character'][tag] = score

            # Add to tag_list based on confidence thresholds
            if score >= character_thresholds['high']:
                tag_list['high_confidence']['character'][tag] = True
            if score >= character_thresholds['medium']:
                tag_list['medium_confidence']['character'][tag] = True
            if score >= character_thresholds['low']:
                tag_list['low_confidence']['character'][tag] = True

        # Extract IP tags from characters
        for confidence_level in ['high_confidence', 'medium_confidence', 'low_confidence']:
            ip_set = set()
            for char_tag in tag_list[confidence_level]['character'].keys():
                if char_tag in self.character_ip_mapping:
                    ip_set.update(self.character_ip_mapping[char_tag])

            for ip_tag in sorted(ip_set):
                tag_list[confidence_level]['ip'][ip_tag] = True

        return {
            'tag_list': tag_list,
            'raw_scores': raw_scores,
            'inference_time': inference_time,
        }

    def close(self):
        """Cleanup resources"""
        # Move model to CPU and clear cache
        if self.device == "cuda":
            self.model.cpu()
            torch.cuda.empty_cache()
