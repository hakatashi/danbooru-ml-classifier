"""
VLM Captioner Firebase Function

Downloads images from S3, generates captions and moderation ratings using
local VLM models, and saves results to Firestore.
"""

import os
import json
import random
import time
import subprocess
import base64
import requests
import argparse
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone
from firebase_functions import https_fn, options
from firebase_admin import initialize_app, firestore

# Import utility functions
from vlm_utils import (
    parse_moderation_rating,
    detect_repetition_loop,
    MIN_REPETITION_LENGTH,
    REPETITION_THRESHOLD,
)

# Import JSON parsing
import json as json_module

# Load environment variables from .env file for local development
from dotenv import load_dotenv
load_dotenv()

# Set GOOGLE_APPLICATION_CREDENTIALS for Firebase authentication
cred_path = Path(__file__).parent.parent / "danbooru-ml-classifier-firebase-adminsdk-uivsj-3a07a63db5.json"
if cred_path.exists() and "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(cred_path)

# Initialize Firebase app if not already initialized
import firebase_admin
if not firebase_admin._apps:
    initialize_app()

# ============================================================================
# CONFIGURATION
# ============================================================================

# Local directories
LOCAL_IMAGE_DIR = Path.home() / "Images" / "hakataarchive" / "twitter"
S3_FILE_CACHE = Path(__file__).parent.parent / ".cache" / "twitter_s3_files.json"
PROMPTS_DIR = Path(__file__).parent / "prompts"

# llama.cpp server configuration
JOYCAPTION_DIR = Path.home() / "Documents" / "GitHub" / "joycaption"
LLAMA_CPP_DIR = JOYCAPTION_DIR / "llama.cpp"
LLAMA_SERVER = LLAMA_CPP_DIR / "build" / "bin" / "llama-server"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8080
SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"

# Use joycaption venv for VLM operations (has ROCm PyTorch and latest transformers)
JOYCAPTION_VENV_PYTHON = JOYCAPTION_DIR / "venv" / "bin" / "python"

# Model configurations
MODELS = {
    "minicpm": {
        "name": "Huihui MiniCPM-V 4.5 Abliterated (F16)",
        "backend": "llama.cpp",
        "repository": "huihui-ai/Huihui-MiniCPM-V-4_5-abliterated",
        "language_file": "GGUF/ggml-model-f16.gguf",
        "vision_file": "GGUF/mmproj-model-f16.gguf",
    },
    "joycaption": {
        "name": "JoyCaption Beta One (Llava F16)",
        "backend": "llama.cpp",
        "language_repository": "concedo/llama-joycaption-beta-one-hf-llava-mmproj-gguf",
        "language_file": "Llama-Joycaption-Beta-One-Hf-Llava-F16.gguf",
        "vision_repository": "concedo/llama-joycaption-beta-one-hf-llava-mmproj-gguf",
        "vision_file": "llama-joycaption-beta-one-llava-mmproj-model-f16.gguf",
    },
    "qwen3": {
        "name": "Qwen3-32B (Q6_K)",
        "backend": "llama.cpp",
        "repository": "Qwen/Qwen3-32B-GGUF",
        "language_file": "Qwen3-32B-Q6_K.gguf",
        "vision_file": None,  # Text-only model
    },
}

# Load prompts from files
def load_prompt(filename: str) -> str:
    """Load prompt text from prompts directory"""
    prompt_path = PROMPTS_DIR / filename
    with open(prompt_path, 'r', encoding='utf-8') as f:
        return f.read().strip()

CAPTION_PROMPT = load_prompt('caption.txt')
MODERATION_PROMPT = load_prompt('moderation.txt')
EXPLANATION_PROMPT = load_prompt('explanation.txt')
AGE_ESTIMATION_PROMPT = load_prompt('age_estimation_from_caption.txt')

# Batch size for processing images per model load
BATCH_SIZE = 10

# Repetition detection configuration
MAX_RETRIES = 3  # Maximum number of retries when repetition is detected
# MIN_REPETITION_LENGTH and REPETITION_THRESHOLD are imported from vlm_utils

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_s3_file_list():
    """Load the S3 file list from cache"""
    if not S3_FILE_CACHE.exists():
        print(f"S3 file cache not found at {S3_FILE_CACHE}")
        return []

    with open(S3_FILE_CACHE, 'r') as f:
        return json.load(f)


def get_local_files():
    """Get set of already downloaded files"""
    if not LOCAL_IMAGE_DIR.exists():
        LOCAL_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        return set()

    return set(f.name for f in LOCAL_IMAGE_DIR.iterdir() if f.is_file())


def get_files_to_download(s3_files, local_files, count=BATCH_SIZE):
    """Get list of files that need to be downloaded"""
    # Filter S3 files to only those not yet downloaded
    not_downloaded = [
        f for f in s3_files
        if Path(f['key']).name not in local_files
    ]

    if not not_downloaded:
        return []

    # Randomly select files
    random.shuffle(not_downloaded)
    return not_downloaded[:count]


def download_from_s3(s3_uri, local_path, s3_key, db):
    """Download a file from S3 using aws-cli"""
    try:
        result = subprocess.run(
            ['aws', 's3', 'cp', s3_uri, str(local_path)],
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode != 0:
            print(f"Error downloading {s3_uri}: {result.stderr}")
            return False

        # Update timestamp to current time
        Path(local_path).touch()

        # Save basic metadata to Firestore
        doc_id = quote(s3_key, safe='')
        doc_ref = db.collection('images').document(doc_id)
        file_type = s3_key.split('/')[0]  # e.g., "twitter"
        file_id = s3_key.split('/')[1].split('.')[0]  # e.g., "abcde"

        doc_ref.set({
            "key": s3_key,
            "status": "liked",
            "type": file_type,
            "postId": file_id,
        }, merge=True)

        return True
    except Exception as e:
        print(f"Exception downloading {s3_uri}: {e}")
        return False


def encode_image_to_base64(image_path):
    """Encode image to base64 for API request"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


# parse_moderation_rating and detect_repetition_loop are now imported from vlm_utils


def parse_age_estimation(raw_response):
    """Parse age estimation JSON response from VLM

    Args:
        raw_response: Raw string response from VLM

    Returns:
        dict: Parsed age estimation data, or None on error
    """
    if not raw_response:
        return None

    try:
        # Try to extract JSON from the response
        # Sometimes the model includes text before/after the JSON
        response = raw_response.strip()

        # Look for JSON object boundaries
        start_idx = response.find('{')
        end_idx = response.rfind('}')

        if start_idx == -1 or end_idx == -1:
            print(f"No JSON object found in response: {response[:100]}")
            return None

        json_str = response[start_idx:end_idx + 1]
        data = json_module.loads(json_str)

        # Validate the structure
        if not isinstance(data, dict):
            print(f"Response is not a dict: {type(data)}")
            return None

        if 'characters_detected' not in data or 'characters' not in data:
            print(f"Missing required fields in response")
            return None

        return data

    except json_module.JSONDecodeError as e:
        print(f"Failed to parse age estimation JSON: {e}")
        print(f"Raw response: {raw_response[:200]}")
        return None
    except Exception as e:
        print(f"Unexpected error parsing age estimation: {e}")
        return None


# ============================================================================
# LLAMA.CPP SERVER MANAGEMENT
# ============================================================================

def get_model_paths(model_key):
    """Get model file paths from HuggingFace Hub"""

    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
    from huggingface_hub import hf_hub_download

    model_config = MODELS[model_key]

    # Handle models with separate repositories for language and vision
    language_repo = model_config.get("language_repository") or model_config.get("repository")
    vision_repo = model_config.get("vision_repository") or model_config.get("repository")

    print(f"Downloading language model from {language_repo}...")

    language_model_path = hf_hub_download(
        repo_id=language_repo,
        filename=model_config["language_file"]
    )

    vision_model_path = None
    if model_config.get("vision_file"):
        print(f"Downloading vision model from {vision_repo}...")
        vision_model_path = hf_hub_download(
            repo_id=vision_repo,
            filename=model_config["vision_file"]
        )

    return language_model_path, vision_model_path


def start_llama_server(language_model, vision_model, port=SERVER_PORT, host=SERVER_HOST):
    """Start the llama-server process"""
    if not LLAMA_SERVER.exists():
        print(f"Error: {LLAMA_SERVER} not found")
        return None

    cmd = [
        str(LLAMA_SERVER),
        "-m", language_model,
        "--host", host,
        "--port", str(port),
        "-c", "8192",
        "-ngl", "99",
        "--cont-batching",
        "--slots",
        "--metrics",
        "--threads-http", "4",
        "--timeout", "600",
        "--temp", "0.7",
        "--top-p", "0.8",
        "--top-k", "40",
        "--repeat-penalty", "1.05",
    ]

    if vision_model:
        cmd.insert(3, "--mmproj")
        cmd.insert(4, vision_model)

    print(f"Starting llama-server...")
    print(f"Command: {' '.join(cmd)}")

    try:
        process = subprocess.Popen(
            cmd,
            cwd=LLAMA_SERVER.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
            start_new_session=True,  # 新しいセッションを開始（子プロセス管理のため）
        )
        return process
    except Exception as e:
        print(f"Error starting server: {e}")
        return None


def wait_for_server(url, timeout=180):
    """Wait for server to be ready"""
    print("Waiting for server to start...")
    start_time = time.time()
    time.sleep(5)

    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{url}/health", timeout=5)
            if response.status_code == 200:
                print("Server is ready!")
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(2)

    print("Server failed to start within timeout period")
    return False


def stop_server(process):
    """Stop the llama-server process"""
    if process:
        # 子プロセスも含めて終了させる
        import signal
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

        print("Server stopped.")

        # ポートが解放されるまで少し待つ
        time.sleep(3)


# ============================================================================
# VLM INFERENCE FUNCTIONS
# ============================================================================

def chat_with_image_llama_api(image_path, messages, server_url=SERVER_URL, max_retries=MAX_RETRIES):
    """Send chat request with image to llama-server API with repetition detection

    Args:
        image_path: Path to the image file
        messages: List of message dicts with role and content
        server_url: URL of the llama-server
        max_retries: Maximum number of retries when repetition is detected

    Returns:
        str: Response content, or None on error
    """
    if not os.path.exists(image_path):
        print(f"Error: Image {image_path} not found")
        return None

    try:
        image_base64 = encode_image_to_base64(image_path)
    except Exception as e:
        print(f"Error encoding image: {e}")
        return None

    # Retry loop for handling repetition
    for attempt in range(max_retries):
        # Build API messages
        api_messages = []
        for i, msg in enumerate(messages):
            if i == 0 and msg["role"] == "user":
                # First user message includes the image
                api_messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": msg["content"]},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                })
            else:
                api_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })

        # Vary temperature slightly on retries to get different outputs
        temperature = 0.7 + (attempt * 0.05)  # 0.7, 0.75, 0.8, ...
        top_p = 0.8 + (attempt * 0.05)  # 0.8, 0.85, 0.9, ...

        request_data = {
            "messages": api_messages,
            "max_tokens": 4096,
            "temperature": min(temperature, 1.0),
            "top_p": min(top_p, 0.95),
            "stream": False,
        }

        try:
            response = requests.post(
                f"{server_url}/v1/chat/completions",
                json=request_data,
                headers={"Content-Type": "application/json"},
                timeout=600
            )

            if response.status_code == 200:
                result = response.json()
                if 'choices' in result and len(result['choices']) > 0:
                    content = result['choices'][0]['message']['content'].strip()

                    # Check for repetition
                    is_repetitive, repeated_phrase, count = detect_repetition_loop(content)

                    if is_repetitive:
                        print(f"⚠️  Repetition detected (attempt {attempt + 1}/{max_retries}):")
                        print(f"   Phrase: '{repeated_phrase[:50]}...' repeated {count} times")

                        if attempt < max_retries - 1:
                            print(f"   Retrying with different sampling parameters...")
                            time.sleep(1)  # Brief pause before retry
                            continue
                        else:
                            print(f"   Max retries reached. Returning result despite repetition.")
                            return content
                    else:
                        # No repetition detected, return the result
                        if attempt > 0:
                            print(f"✓ Retry successful on attempt {attempt + 1}")
                        return content
            else:
                print(f"Error: Server returned status {response.status_code}")
                print(f"Response: {response.text}")
                return None

        except Exception as e:
            print(f"Error communicating with server: {e}")
            return None

    return None


def chat_continuation_llama_api(messages, server_url=SERVER_URL):
    """Continue chat without image (for moderation prompt after caption)

    Args:
        messages: List of message dicts with role and content
        server_url: URL of the llama-server

    Returns:
        str: Response content, or None on error
    """
    request_data = {
        "messages": messages,
        "max_tokens": 256,
        "temperature": 0.3,
        "top_p": 0.8,
        "stream": False,
    }

    try:
        response = requests.post(
            f"{server_url}/v1/chat/completions",
            json=request_data,
            headers={"Content-Type": "application/json"},
            timeout=120
        )

        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                return result['choices'][0]['message']['content'].strip()
        else:
            print(f"Error: Server returned status {response.status_code}")
        return None

    except Exception as e:
        print(f"Error communicating with server: {e}")
        return None


def chat_text_only_llama_api(messages, server_url=SERVER_URL, max_tokens=2048, temperature=0.7, top_p=0.9):
    """Send chat request for text-only inference (no image)

    Args:
        messages: List of message dicts with role and content
        server_url: URL of the llama-server
        max_tokens: Maximum tokens to generate
        temperature: Sampling temperature
        top_p: Nucleus sampling parameter

    Returns:
        str: Response content, or None on error
    """
    request_data = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stream": False,
    }

    try:
        response = requests.post(
            f"{server_url}/v1/chat/completions",
            json=request_data,
            headers={"Content-Type": "application/json"},
            timeout=300
        )

        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                return result['choices'][0]['message']['content'].strip()
        else:
            print(f"Error: Server returned status {response.status_code}")
            print(f"Response: {response.text}")
        return None

    except Exception as e:
        print(f"Error communicating with server: {e}")
        return None


# ============================================================================
# MAIN PROCESSING FUNCTIONS
# ============================================================================

def process_images_with_minicpm(image_paths, db, generate_explanation=False):
    """Process batch of images with MiniCPM model using llama.cpp

    Args:
        image_paths: List of paths to images to process
        db: Firestore database instance
        generate_explanation: Whether to generate explanation for moderation rating (default: False)
    """
    model_key = "minicpm"
    model_config = MODELS[model_key]

    # Get model paths
    language_model, vision_model = get_model_paths(model_key)

    # Start server
    server_process = start_llama_server(language_model, vision_model)
    if not server_process:
        print("Failed to start llama server for MiniCPM")
        return

    try:
        if not wait_for_server(SERVER_URL):
            print("Server failed to become ready")
            return

        for image_path in image_paths:
            print(f"Processing with MiniCPM: {image_path}")

            # Generate caption
            messages = [{"role": "user", "content": CAPTION_PROMPT}]
            caption = chat_with_image_llama_api(str(image_path), messages)

            if not caption:
                print(f"Failed to generate caption for {image_path}")
                continue

            print(f"Caption generated ({len(caption)} chars)")

            # Continue with moderation prompt
            moderation_raw = chat_with_image_llama_api(str(image_path), [
                {"role": "user", "content": CAPTION_PROMPT},
                {"role": "assistant", "content": caption},
                {"role": "user", "content": MODERATION_PROMPT}
            ])

            if not moderation_raw:
                print(f"Failed to generate moderation for {image_path}")
                continue

            moderation_result = parse_moderation_rating(moderation_raw)
            print(f"Moderation rating: {moderation_result}")

            # Generate explanation for the moderation rating (only if enabled)
            explanation = None
            if generate_explanation:
                explanation = chat_with_image_llama_api(str(image_path), [
                    {"role": "user", "content": CAPTION_PROMPT},
                    {"role": "assistant", "content": caption},
                    {"role": "user", "content": MODERATION_PROMPT},
                    {"role": "assistant", "content": moderation_raw},
                    {"role": "user", "content": EXPLANATION_PROMPT}
                ])

                if not explanation:
                    print(f"Failed to generate explanation for {image_path}")
                    explanation = None
                else:
                    print(f"Explanation generated ({len(explanation)} chars)")

            # Save to Firestore (age estimation will be done separately with Qwen3)
            save_to_firestore(
                db, image_path, model_key, model_config,
                caption, moderation_raw, moderation_result, explanation
            )

    finally:
        stop_server(server_process)


def process_images_with_joycaption(image_paths, db, generate_explanation=False):
    """Process batch of images with JoyCaption model using llama.cpp

    Args:
        image_paths: List of paths to images to process
        db: Firestore database instance
        generate_explanation: Whether to generate explanation for moderation rating (default: False)
    """
    model_key = "joycaption"
    model_config = MODELS[model_key]

    # Get model paths
    language_model, vision_model = get_model_paths(model_key)

    # Start server
    server_process = start_llama_server(language_model, vision_model)
    if not server_process:
        print("Failed to start llama server for JoyCaption")
        return

    try:
        if not wait_for_server(SERVER_URL):
            print("Server failed to become ready")
            return

        for image_path in image_paths:
            print(f"Processing with JoyCaption: {image_path}")

            # Generate caption
            messages = [{"role": "user", "content": CAPTION_PROMPT}]
            caption = chat_with_image_llama_api(str(image_path), messages)

            if not caption:
                print(f"Failed to generate caption for {image_path}")
                continue

            print(f"Caption generated ({len(caption)} chars)")

            # Generate moderation rating with image context
            moderation_raw = chat_with_image_llama_api(str(image_path), [
                {"role": "user", "content": CAPTION_PROMPT},
                {"role": "assistant", "content": caption},
                {"role": "user", "content": MODERATION_PROMPT}
            ])

            if not moderation_raw:
                print(f"Failed to generate moderation for {image_path}")
                continue

            moderation_result = parse_moderation_rating(moderation_raw)
            print(f"Moderation rating: {moderation_result}")

            # Generate explanation for the moderation rating (only if enabled)
            explanation = None
            if generate_explanation:
                explanation = chat_with_image_llama_api(str(image_path), [
                    {"role": "user", "content": CAPTION_PROMPT},
                    {"role": "assistant", "content": caption},
                    {"role": "user", "content": MODERATION_PROMPT},
                    {"role": "assistant", "content": moderation_raw},
                    {"role": "user", "content": EXPLANATION_PROMPT}
                ])

                if not explanation:
                    print(f"Failed to generate explanation for {image_path}")
                    explanation = None
                else:
                    print(f"Explanation generated ({len(explanation)} chars)")

            # Save to Firestore (age estimation will be done separately with Qwen3)
            save_to_firestore(
                db, image_path, model_key, model_config,
                caption, moderation_raw, moderation_result, explanation
            )

    finally:
        stop_server(server_process)


def save_to_firestore(db, image_path, model_key, model_config, caption, moderation_raw, moderation_result, explanation=None, age_estimation_raw=None, age_estimation_result=None):
    """Save caption, moderation, and age estimation results to Firestore"""
    image_name = Path(image_path).name
    # Use URL-encoded S3 path as document ID (e.g., "twitter/abcde.png" -> "twitter%2Fabcde.png")
    s3_key = f"twitter/{image_name}"
    doc_id = quote(s3_key, safe='')
    doc_ref = db.collection('images').document(doc_id)

    # Handle both single repository and separate language/vision repositories
    language_repo = model_config.get("language_repository") or model_config.get("repository")
    vision_repo = model_config.get("vision_repository") or model_config.get("repository")

    # Get current timestamp
    current_time = datetime.now(timezone.utc)

    caption_data = {
        "metadata": {
            "model": model_config["name"],
            "backend": model_config["backend"],
            "language_repository": language_repo,
            "vision_repository": vision_repo,
            "language_file": model_config.get("language_file"),
            "vision_file": model_config.get("vision_file"),
            "prompt": CAPTION_PROMPT,
            "createdAt": current_time,
        },
        "caption": caption,
    }

    moderation_data = {
        "metadata": {
            "model": model_config["name"],
            "backend": model_config["backend"],
            "language_repository": language_repo,
            "vision_repository": vision_repo,
            "language_file": model_config.get("language_file"),
            "vision_file": model_config.get("vision_file"),
            "prompt": MODERATION_PROMPT,
            "createdAt": current_time,
        },
        "raw_result": moderation_raw,
        "result": moderation_result,
    }

    # Add explanation if provided (only for MiniCPM)
    if explanation is not None:
        moderation_data["explanation"] = explanation

    # Get existing document data or create new structure
    doc = doc_ref.get()
    if doc.exists:
        existing_data = doc.to_dict()
    else:
        existing_data = {}

    # Update captions and moderations with nested structure
    captions = existing_data.get("captions", {})
    moderations = existing_data.get("moderations", {})

    captions[model_key] = caption_data
    moderations[model_key] = moderation_data

    # Prepare update data
    update_data = {
        "captions": captions,
        "moderations": moderations,
    }

    # Add age estimation if provided
    if age_estimation_raw is not None and age_estimation_result is not None:
        age_estimation_data = {
            "metadata": {
                "model": model_config["name"],
                "backend": model_config["backend"],
                "language_repository": language_repo,
                "vision_repository": vision_repo,
                "language_file": model_config.get("language_file"),
                "vision_file": model_config.get("vision_file"),
                "prompt": AGE_ESTIMATION_PROMPT,
                "createdAt": current_time,
            },
            "raw_result": age_estimation_raw,
            "result": age_estimation_result,
        }

        # Pre-calculate main character's estimated age (first character)
        main_character_age = None
        if (age_estimation_result.get("characters_detected", 0) > 0 and
            len(age_estimation_result.get("characters", [])) > 0):
            first_character = age_estimation_result["characters"][0]
            main_character_age = first_character.get("most_likely_age")

        age_estimation_data["main_character_age"] = main_character_age

        age_estimations = existing_data.get("ageEstimations", {})
        age_estimations[model_key] = age_estimation_data
        update_data["ageEstimations"] = age_estimations

    # Use merge to update the document
    doc_ref.set(update_data, merge=True)

    print(f"Saved results for {doc_id} with model {model_key}")


def run_vlm_captioner(models=None, generate_explanation=False):
    """Main function to run VLM captioning

    Args:
        models: List of model names to run (e.g., ['minicpm', 'joycaption']).
                If None, defaults to ['minicpm'] only.
        generate_explanation: Whether to generate explanation for moderation rating (default: False)
    """
    # Default to minicpm only if not specified
    if models is None:
        models = ['minicpm']

    # Validate model names
    valid_models = set(MODELS.keys())
    invalid_models = set(models) - valid_models
    if invalid_models:
        print(f"Error: Invalid model names: {invalid_models}")
        print(f"Valid models are: {valid_models}")
        return

    db = firestore.client()

    start_time = time.time()
    duration = 12 * 60 * 60  # 12 hours

    print("Starting VLM Captioner")
    print(f"Models to run: {models}")
    print(f"Generate explanation: {generate_explanation}")
    print(f"Will run for {duration // 60} minutes")
    print(f"Local image directory: {LOCAL_IMAGE_DIR}")
    print(f"S3 file cache: {S3_FILE_CACHE}")

    # Load S3 file list
    s3_files = get_s3_file_list()
    print(f"Found {len(s3_files)} files in S3 cache")

    elapsed = time.time() - start_time
    remaining = duration - elapsed
    print(f"\n--- Elapsed: {elapsed/60:.1f}min, Remaining: {remaining/60:.1f}min ---")

    # Get local files
    local_files = get_local_files()
    print(f"Found {len(local_files)} local files")

    # Get files to download
    files_to_download = get_files_to_download(s3_files, local_files, BATCH_SIZE)

    if not files_to_download:
        print("No more files to download")
        return

    print(f"Downloading {len(files_to_download)} files from S3...")

    # Download files
    downloaded_paths = []
    for file_info in files_to_download:
        local_path = LOCAL_IMAGE_DIR / Path(file_info['key']).name
        if download_from_s3(file_info['s3_uri'], local_path, file_info['key'], db):
            downloaded_paths.append(local_path)
            print(f"Downloaded: {local_path.name}")
        else:
            print(f"Failed to download: {file_info['key']}")

    if not downloaded_paths:
        print("No files were downloaded")
        return

    # Process with selected models
    if 'joycaption' in models:
        print(f"\n=== Processing {len(downloaded_paths)} images with JoyCaption ===")
        process_images_with_joycaption(downloaded_paths, db, generate_explanation)

    if 'minicpm' in models:
        print(f"\n=== Processing {len(downloaded_paths)} images with MiniCPM ===")
        process_images_with_minicpm(downloaded_paths, db, generate_explanation)

    print("\nVLM Captioner finished")
    print(f"Total runtime: {(time.time() - start_time) / 60:.1f} minutes")


# ============================================================================
# FIREBASE FUNCTION
# ============================================================================

@https_fn.on_request(
    memory=options.MemoryOption.GB_16,
    cpu=4,
    timeout_sec=600,
    region="us-central1",
)
def runVlmCaptioner(request: https_fn.Request) -> https_fn.Response:
    """HTTP trigger to run VLM captioning

    This function:
    1. Downloads images from S3 that haven't been processed yet
    2. Generates captions using JoyCaption and MiniCPM models
    3. Generates moderation ratings for each image
    4. Saves results to Firestore

    Runs for approximately 10 minutes per invocation.
    """
    try:
        run_vlm_captioner()
        return https_fn.Response("VLM Captioner completed successfully", status=200)
    except Exception as e:
        import traceback
        error_msg = f"Error: {str(e)}\n{traceback.format_exc()}"
        print(error_msg)
        return https_fn.Response(error_msg, status=500)


# For local testing
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="VLM Captioner - Generate captions and moderation ratings for images"
    )
    parser.add_argument(
        "--models",
        nargs="+",
        choices=list(MODELS.keys()),
        default=["minicpm"],
        help="Models to run (default: minicpm only). Can specify multiple models: --models minicpm joycaption"
    )
    parser.add_argument(
        "--generate-explanation",
        action="store_true",
        default=False,
        help="Generate explanation for moderation rating (default: False)"
    )

    args = parser.parse_args()

    print(f"Command-line arguments:")
    print(f"  Models: {args.models}")
    print(f"  Generate explanation: {args.generate_explanation}")
    print()

    run_vlm_captioner(models=args.models, generate_explanation=args.generate_explanation)
