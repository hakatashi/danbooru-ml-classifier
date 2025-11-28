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
    chat_with_image_llama_api,
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


def get_images_without_age_estimation(db, model_key, limit=100):
    """Get images that don't have age estimation for the specified model

    Args:
        db: Firestore database instance
        model_key: Model name (e.g., 'minicpm', 'joycaption')
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

    # Filter for images that have captions but not age estimation for this model
    images_without_age = []
    for doc in docs:
        data = doc.to_dict()

        # Check if this model's caption exists (meaning it's been processed)
        captions = data.get('captions', {})
        if model_key not in captions:
            continue

        # Check if this model's age estimation already exists
        age_estimations = data.get('ageEstimations', {})
        if model_key in age_estimations:
            continue

        # This image has caption but no age estimation
        images_without_age.append(doc)

        # Stop when we have enough
        if len(images_without_age) >= limit:
            break

    return images_without_age


def process_image_age_estimation(db, doc, model_key, model_config, server_url=SERVER_URL):
    """Process a single image for age estimation

    Args:
        db: Firestore database instance
        doc: Firestore document snapshot
        model_key: Model name (e.g., 'minicpm', 'joycaption')
        model_config: Model configuration dict
        server_url: URL of the llama-server

    Returns:
        bool: True if successful, False otherwise
    """
    doc_id = doc.id
    data = doc.to_dict()

    # Get the image key and decode it
    s3_key = data.get('key')
    if not s3_key:
        print(f"No key found for document {doc_id}")
        return False

    # Construct local image path
    image_name = Path(s3_key).name
    image_path = LOCAL_IMAGE_DIR / image_name

    if not image_path.exists():
        print(f"Image not found locally: {image_path}")
        return False

    print(f"Processing age estimation for: {image_name}")

    # Generate age estimation
    age_estimation_raw = chat_with_image_llama_api(str(image_path), [
        {"role": "user", "content": AGE_ESTIMATION_PROMPT}
    ], server_url=server_url)

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
    language_repo = model_config.get("language_repository") or model_config.get("repository")
    vision_repo = model_config.get("vision_repository") or model_config.get("repository")
    current_time = datetime.now(timezone.utc)

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

    # Update Firestore
    doc_ref = db.collection('images').document(doc_id)
    doc_ref.set({
        "ageEstimations": {
            model_key: age_estimation_data
        }
    }, merge=True)

    print(f"✓ Saved age estimation for {doc_id} (main character age: {main_character_age})")
    return True


def run_backfill(model_key='minicpm', batch_size=50, max_images=None):
    """Main function to backfill age estimations

    Args:
        model_key: Model to use for age estimation (default: 'minicpm')
        batch_size: Number of images to process per server session
        max_images: Maximum total images to process (None for unlimited)
    """
    if model_key not in MODELS:
        print(f"Error: Invalid model key '{model_key}'")
        print(f"Valid models: {list(MODELS.keys())}")
        return

    model_config = MODELS[model_key]
    db = firestore.client()

    print(f"Starting age estimation backfill with {model_key}")
    print(f"Batch size: {batch_size}")
    print(f"Max images: {max_images or 'unlimited'}")

    total_processed = 0
    total_successful = 0
    total_failed = 0

    # Get model paths
    print("\nLoading model...")
    language_model, vision_model = get_model_paths(model_key)

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
            images = get_images_without_age_estimation(db, model_key, limit=fetch_limit)

            if not images:
                print("No more images to process")
                break

            print(f"Found {len(images)} images to process")

            # Process each image
            batch_successful = 0
            batch_failed = 0

            for i, doc in enumerate(images):
                print(f"\n[{i+1}/{len(images)}] ", end="")

                if process_image_age_estimation(db, doc, model_key, model_config):
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
        description="Backfill age estimation data for existing images"
    )
    parser.add_argument(
        "--model",
        choices=list(MODELS.keys()),
        default="minicpm",
        help="Model to use for age estimation (default: minicpm)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of images to process per batch (default: 50)"
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Maximum total images to process (default: unlimited)"
    )

    args = parser.parse_args()

    print(f"Configuration:")
    print(f"  Model: {args.model}")
    print(f"  Batch size: {args.batch_size}")
    print(f"  Max images: {args.max_images or 'unlimited'}")
    print()

    run_backfill(
        model_key=args.model,
        batch_size=args.batch_size,
        max_images=args.max_images
    )
