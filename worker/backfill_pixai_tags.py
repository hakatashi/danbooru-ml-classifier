"""
Backfill PixAI tags for existing images in Firestore

This script processes all images in ~/Images/hakataarchive/twitter directory,
generates PixAI tags, and updates Firestore documents.
"""

import os
import sys
import time
import signal
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone

from PIL import Image
from firebase_admin import initialize_app, firestore
from dotenv import load_dotenv

from pixai_tagger import PixAITagger

# Load environment variables
load_dotenv()

# Set GOOGLE_APPLICATION_CREDENTIALS for Firebase authentication
cred_path = Path(__file__).parent.parent / "danbooru-ml-classifier-firebase-adminsdk-uivsj-3a07a63db5.json"
if cred_path.exists() and "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(cred_path)

# Initialize Firebase app if not already initialized
import firebase_admin
if not firebase_admin._apps:
    initialize_app()

# Configuration
LOCAL_IMAGE_DIR = Path.home() / "Images" / "hakataarchive" / "twitter"
PIXAI_MODEL_DIR = Path.home() / ".cache" / "pixai-tagger"

# Model configuration
MODEL_KEY = "pixai"
MODEL_CONFIG = {
    "name": "PixAI Tagger v0.9",
    "backend": "pytorch",
    "repository": "pixai-labs/pixai-tagger-v0.9",
    "model_dir": PIXAI_MODEL_DIR,
}

# Global flag for graceful shutdown
_shutdown_requested = False

def signal_handler(sig, frame):
    """Handle SIGINT (Ctrl+C) for graceful shutdown"""
    global _shutdown_requested
    if not _shutdown_requested:
        print("\n\n⚠️  Shutdown requested. Will finish processing current image and exit gracefully...")
        _shutdown_requested = True
    else:
        print("\n⚠️  Second interrupt received. Forcing exit...")
        sys.exit(1)

# Register signal handler
signal.signal(signal.SIGINT, signal_handler)


def get_image_files():
    """Get all image files from the local directory"""
    if not LOCAL_IMAGE_DIR.exists():
        print(f"Error: Directory not found: {LOCAL_IMAGE_DIR}")
        return []

    image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
    image_files = [
        f for f in LOCAL_IMAGE_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in image_extensions
    ]

    return sorted(image_files)


def get_firestore_doc_id(image_path: Path) -> str:
    """Get Firestore document ID from image path"""
    image_name = image_path.name
    s3_key = f"twitter/{image_name}"
    doc_id = quote(s3_key, safe='')
    return doc_id


def check_if_tags_exist(db, doc_id: str) -> bool:
    """Check if PixAI tags already exist for this document"""
    doc_ref = db.collection('images').document(doc_id)
    doc = doc_ref.get()

    if not doc.exists:
        return False

    data = doc.to_dict()
    tags = data.get("tags", {})
    return MODEL_KEY in tags


def save_pixai_tags_to_firestore(db, image_path: Path, result: dict):
    """Save PixAI tagging results to Firestore"""
    doc_id = get_firestore_doc_id(image_path)
    doc_ref = db.collection('images').document(doc_id)

    # Get current timestamp
    current_time = datetime.now(timezone.utc)

    # Prepare tag data according to the specified structure
    tag_data = {
        "tag_list": result["tag_list"],
        "raw_scores": result["raw_scores"],
        "metadata": {
            "model": MODEL_CONFIG["name"],
            "backend": MODEL_CONFIG["backend"],
            "repository": MODEL_CONFIG["repository"],
            "inference_time": result["inference_time"],
            "createdAt": current_time,
        },
    }

    # Get existing document data or create new structure
    doc = doc_ref.get()
    if doc.exists:
        existing_data = doc.to_dict()
    else:
        existing_data = {}

    # Update tags with nested structure
    tags = existing_data.get("tags", {})
    tags[MODEL_KEY] = tag_data

    # Prepare update data
    update_data = {
        "tags": tags,
    }

    # Use merge to update the document
    doc_ref.set(update_data, merge=True)


def backfill_pixai_tags(skip_existing=True, max_images=None):
    """
    Backfill PixAI tags for all existing images

    Args:
        skip_existing: If True, skip images that already have PixAI tags
        max_images: Maximum number of images to process (None for all)
    """
    global _shutdown_requested

    print("=" * 80)
    print("PixAI Tags Backfill Script")
    print("=" * 80)
    print(f"Image directory: {LOCAL_IMAGE_DIR}")
    print(f"Model directory: {PIXAI_MODEL_DIR}")
    print(f"Skip existing: {skip_existing}")
    print(f"Max images: {max_images or 'unlimited'}")
    print("\n💡 Press Ctrl+C to gracefully stop after completing current image\n")

    # Initialize Firestore
    db = firestore.client()

    # Get all image files
    print("Scanning image directory...")
    image_files = get_image_files()
    print(f"Found {len(image_files)} image files")

    if not image_files:
        print("No image files found. Exiting.")
        return

    # Initialize PixAI tagger
    print("\n[PixAI] Initializing tagger...")
    tagger = PixAITagger(model_dir=PIXAI_MODEL_DIR)

    # Process images
    processed_count = 0
    skipped_count = 0
    error_count = 0
    start_time = time.time()

    try:
        for i, image_path in enumerate(image_files, 1):
            if _shutdown_requested:
                print("\n✓ Shutdown requested. Stopping gracefully.")
                break

            if max_images and processed_count >= max_images:
                print(f"\n✓ Reached maximum number of images ({max_images}). Stopping.")
                break

            doc_id = get_firestore_doc_id(image_path)

            # Check if tags already exist
            if skip_existing and check_if_tags_exist(db, doc_id):
                print(f"[{i}/{len(image_files)}] SKIP: {image_path.name} (tags already exist)")
                skipped_count += 1
                continue

            try:
                print(f"\n[{i}/{len(image_files)}] Processing: {image_path.name}")

                # Open and tag image
                image = Image.open(image_path)
                result = tagger.tag_image(image)

                print(f"  Inference time: {result['inference_time']:.3f}s")
                print(f"  High confidence tags:")
                print(f"    - Features: {len(result['tag_list']['high_confidence']['feature'])}")
                print(f"    - Characters: {len(result['tag_list']['high_confidence']['character'])}")
                print(f"    - IPs: {len(result['tag_list']['high_confidence']['ip'])}")
                print(f"  Raw scores saved:")
                print(f"    - Features: {len(result['raw_scores']['feature'])}")
                print(f"    - Characters: {len(result['raw_scores']['character'])}")

                # Save to Firestore
                save_pixai_tags_to_firestore(db, image_path, result)
                print(f"  ✓ Saved to Firestore: {doc_id}")

                processed_count += 1

                # Print progress summary every 10 images
                if processed_count % 10 == 0:
                    elapsed = time.time() - start_time
                    avg_time = elapsed / processed_count
                    remaining_images = len(image_files) - i
                    if max_images:
                        remaining_images = min(remaining_images, max_images - processed_count)
                    estimated_remaining = avg_time * remaining_images
                    print(f"\n  📊 Progress: {processed_count} processed, {skipped_count} skipped, {error_count} errors")
                    print(f"  ⏱️  Average: {avg_time:.2f}s/image, Estimated remaining: {estimated_remaining/60:.1f}min\n")

            except Exception as e:
                print(f"  ❌ Error processing {image_path.name}: {e}")
                error_count += 1
                continue

    finally:
        tagger.close()

    # Print final summary
    elapsed = time.time() - start_time
    print("\n" + "=" * 80)
    print("BACKFILL SUMMARY")
    print("=" * 80)
    print(f"Total images found: {len(image_files)}")
    print(f"Processed: {processed_count}")
    print(f"Skipped (existing): {skipped_count}")
    print(f"Errors: {error_count}")
    print(f"Total time: {elapsed/60:.1f} minutes")
    if processed_count > 0:
        print(f"Average time per image: {elapsed/processed_count:.2f} seconds")
    print("=" * 80)

    if _shutdown_requested:
        print("\n✓ Graceful shutdown completed")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill PixAI tags for existing images in Firestore"
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Process all images, even if they already have PixAI tags"
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Maximum number of images to process (default: unlimited)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without actually processing"
    )

    args = parser.parse_args()

    if args.dry_run:
        print("🔍 DRY RUN MODE - No actual processing will occur\n")
        image_files = get_image_files()
        print(f"Would process up to {len(image_files)} images")
        if args.max_images:
            print(f"Limited to {args.max_images} images")
        print(f"Skip existing: {not args.no_skip_existing}")

        # Sample first few files
        print("\nSample files:")
        for image_path in image_files[:5]:
            print(f"  - {image_path.name}")
        if len(image_files) > 5:
            print(f"  ... and {len(image_files) - 5} more")
        return

    backfill_pixai_tags(
        skip_existing=not args.no_skip_existing,
        max_images=args.max_images
    )


if __name__ == "__main__":
    main()
