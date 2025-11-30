"""
Backfill Age Estimation Script

This script processes existing images in Firestore that don't have age estimation data
and generates age estimations using VLM models.
"""

import os
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone
from firebase_admin import initialize_app, firestore
from urllib.parse import unquote

# Import functions from vlm_captioner
from vlm_captioner import (
    MODELS,
    AGE_ESTIMATION_PROMPT,
    LOCAL_IMAGE_DIR,
    chat_text_only_llama_api,
    parse_age_estimation,
    get_model_paths,
    start_llama_server,
    wait_for_server,
    stop_server,
    SERVER_URL,
)

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


def get_images_without_age_estimation(db, caption_model_key, limit=1000):
    """Get images that have captions but don't have age estimation

    Args:
        db: Firestore database instance
        caption_model_key: Caption model to use as source (e.g., 'minicpm', 'joycaption')
        limit: Maximum number of images to fetch

    Returns:
        List of document snapshots
    """
    images_ref = db.collection('images')

    # Firestore doesn't support '!= None', so we need to fetch more documents
    # and filter client-side
    # We'll fetch documents that have been processed (have status field)
    query = images_ref.where('status', '==', 'liked').limit(limit * 3)

    docs = query.stream()

    # Filter for images that have captions but not age estimation
    # Note: Age estimation is now done with a separate model (qwen3)
    images_without_age = []
    for doc in docs:
        data = doc.to_dict()

        # Check if the caption exists (meaning it's been processed)
        captions = data.get('captions', {})
        if caption_model_key not in captions:
            continue

        # Check if age estimation already exists (for any model)
        age_estimations = data.get('ageEstimations', {})
        if 'qwen3' in age_estimations:
            continue

        # This image has caption but no age estimation
        images_without_age.append(doc)

        # Stop when we have enough
        if len(images_without_age) >= limit:
            break

    return images_without_age


def process_image_age_estimation(db, doc, caption_model_key, age_model_key, age_model_config, server_url=SERVER_URL):
    """Process a single image for age estimation based on caption

    Args:
        db: Firestore database instance
        doc: Firestore document snapshot
        caption_model_key: Caption model to use as source (e.g., 'minicpm', 'joycaption')
        age_model_key: Age estimation model key (e.g., 'qwen3')
        age_model_config: Age estimation model configuration dict
        server_url: URL of the llama-server

    Returns:
        bool: True if successful, False otherwise
    """
    doc_id = doc.id
    data = doc.to_dict()

    # Get the image key
    s3_key = data.get('key')
    if not s3_key:
        print(f"No key found for document {doc_id}")
        return False

    image_name = Path(s3_key).name
    print(f"Processing age estimation for: {image_name}")

    # Get existing caption as input for age estimation
    captions = data.get('captions', {})
    existing_caption = captions.get(caption_model_key, {}).get('caption')

    if not existing_caption:
        print(f"No existing caption found for {image_name} with model {caption_model_key}")
        return False

    print(f"Using caption from {caption_model_key} ({len(existing_caption)} chars)")

    # Generate age estimation from caption using text-only inference
    # Format the prompt with the caption embedded
    age_estimation_prompt_with_caption = f"""{AGE_ESTIMATION_PROMPT}

Caption:
{existing_caption}

/no_think
"""

    age_estimation_raw = chat_text_only_llama_api(
        messages=[{"role": "user", "content": age_estimation_prompt_with_caption}],
        server_url=server_url,
        max_tokens=2048,
        temperature=0.7,
        top_p=0.9
    )

    if not age_estimation_raw:
        print(f"Failed to generate age estimation for {image_name}")
        return False

    # Parse age estimation
    age_estimation_result = parse_age_estimation(age_estimation_raw)
    if not age_estimation_result:
        print(f"Failed to parse age estimation for {image_name}")
        return False

    print(f"Age estimation: {age_estimation_result.get('characters_detected', 0)} characters detected")

    # Prepare age estimation data
    language_repo = age_model_config.get("language_repository") or age_model_config.get("repository")
    current_time = datetime.now(timezone.utc)

    age_estimation_data = {
        "metadata": {
            "model": age_model_config["name"],
            "backend": age_model_config["backend"],
            "language_repository": language_repo,
            "language_file": age_model_config.get("language_file"),
            "prompt": AGE_ESTIMATION_PROMPT,
            "caption_source": caption_model_key,  # Track which caption was used
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

    # Update Firestore
    doc_ref = db.collection('images').document(doc_id)
    doc_ref.set({
        "ageEstimations": {
            age_model_key: age_estimation_data
        }
    }, merge=True)

    print(f"✓ Saved age estimation for {doc_id} (main character age: {main_character_age})")
    return True


def run_backfill(caption_model='minicpm', age_model='qwen3', batch_size=50, max_images=None):
    """Main function to backfill age estimations from captions

    Args:
        caption_model: Caption model to use as source (default: 'minicpm')
        age_model: Model to use for age estimation (default: 'qwen3')
        batch_size: Number of images to process per server session
        max_images: Maximum total images to process (None for unlimited)
    """
    if caption_model not in MODELS:
        print(f"Error: Invalid caption model key '{caption_model}'")
        print(f"Valid models: {list(MODELS.keys())}")
        return

    if age_model not in MODELS:
        print(f"Error: Invalid age estimation model key '{age_model}'")
        print(f"Valid models: {list(MODELS.keys())}")
        return

    age_model_config = MODELS[age_model]
    db = firestore.client()

    print(f"Starting age estimation backfill")
    print(f"Caption source: {caption_model}")
    print(f"Age estimation model: {age_model}")
    print(f"Batch size: {batch_size}")
    print(f"Max images: {max_images or 'unlimited'}")

    total_processed = 0
    total_successful = 0
    total_failed = 0

    # Get model paths for age estimation model
    print("\nLoading age estimation model...")
    language_model, vision_model = get_model_paths(age_model)

    # Start server once for all batches
    print("\nStarting llama server...")
    server_process = start_llama_server(language_model, vision_model)
    if not server_process:
        print("Failed to start llama server")
        return

    try:
        if not wait_for_server(SERVER_URL):
            print("Server failed to become ready")
            return

        while True:
            # Check if we've reached max_images limit
            if max_images and total_processed >= max_images:
                print(f"\nReached maximum image limit ({max_images})")
                break

            # Calculate how many images to fetch in this batch
            remaining = max_images - total_processed if max_images else batch_size
            fetch_limit = min(batch_size, remaining) if max_images else batch_size

            # Get images without age estimation
            print(f"\nFetching up to {fetch_limit} images without age estimation...")
            images = get_images_without_age_estimation(db, caption_model, limit=fetch_limit)

            if not images:
                print("No more images to process")
                break

            print(f"Found {len(images)} images to process")

            # Process each image
            batch_successful = 0
            batch_failed = 0

            for i, doc in enumerate(images):
                print(f"\n[{i+1}/{len(images)}] ", end="")

                if process_image_age_estimation(db, doc, caption_model, age_model, age_model_config):
                    batch_successful += 1
                    total_successful += 1
                else:
                    batch_failed += 1
                    total_failed += 1

                total_processed += 1

                # Brief pause between images to avoid overwhelming the server
                time.sleep(0.5)

                # Check if we've reached max_images limit
                if max_images and total_processed >= max_images:
                    break

            print(f"\nBatch complete: {batch_successful} successful, {batch_failed} failed")
            print(f"Total progress: {total_processed} processed ({total_successful} successful, {total_failed} failed)")

            # If we processed fewer images than batch_size, we're done
            if len(images) < fetch_limit:
                print("\nNo more images to process")
                break

    finally:
        print("\nStopping llama server...")
        stop_server(server_process)

    print("\n" + "="*60)
    print("Backfill complete!")
    print(f"Total processed: {total_processed}")
    print(f"Successful: {total_successful}")
    print(f"Failed: {total_failed}")
    print("="*60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill age estimation data for existing images using caption-based inference"
    )
    parser.add_argument(
        "--caption-model",
        choices=list(MODELS.keys()),
        default="minicpm",
        help="Caption model to use as source (default: minicpm)"
    )
    parser.add_argument(
        "--age-model",
        choices=list(MODELS.keys()),
        default="qwen3",
        help="Model to use for age estimation from caption (default: qwen3)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of images to process per batch (default: 1000)"
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Maximum total images to process (default: unlimited)"
    )

    args = parser.parse_args()

    print(f"Configuration:")
    print(f"  Caption model: {args.caption_model}")
    print(f"  Age estimation model: {args.age_model}")
    print(f"  Batch size: {args.batch_size}")
    print(f"  Max images: {args.max_images or 'unlimited'}")
    print()

    run_backfill(
        caption_model=args.caption_model,
        age_model=args.age_model,
        batch_size=args.batch_size,
        max_images=args.max_images
    )
