#!/usr/bin/env python3
"""
Backfill Twitter Source Data to Firestore

Queries Firestore for images with type='twitter' that don't have source data,
and updates them with source metadata from the Twitter media mapping cache.
"""

import os
import json
import argparse
from pathlib import Path
from urllib.parse import unquote

# Set GOOGLE_APPLICATION_CREDENTIALS for Firebase authentication
cred_path = Path(__file__).parent.parent.parent / "danbooru-ml-classifier-firebase-adminsdk-uivsj-3a07a63db5.json"
if cred_path.exists() and "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(cred_path)

# Initialize Firebase
from firebase_admin import initialize_app, firestore
import firebase_admin

if not firebase_admin._apps:
    initialize_app()

# Configuration
TWITTER_MEDIA_MAPPING_CACHE = Path(__file__).parent.parent.parent / ".cache" / "twitter_media_mapping.json"


def load_twitter_media_mapping():
    """Load the Twitter media ID to tweet metadata mapping from cache"""
    if not TWITTER_MEDIA_MAPPING_CACHE.exists():
        print(f"Error: Twitter media mapping cache not found at {TWITTER_MEDIA_MAPPING_CACHE}")
        print("Run worker/scripts/build_twitter_media_mapping.py to generate it")
        return None

    with open(TWITTER_MEDIA_MAPPING_CACHE, 'r') as f:
        return json.load(f)


def extract_media_id_from_key(s3_key):
    """Extract media ID from S3 key

    Args:
        s3_key: S3 key like "twitter/FVYuylQagAU2WHq.jpg" or "twitter/1597464275324268544"

    Returns:
        str: Media ID like "FVYuylQagAU2WHq.jpg" or "1597464275324268544"
    """
    if not s3_key:
        return None

    parts = s3_key.split('/')
    if len(parts) < 2:
        return None

    return parts[1]  # Return the filename part


def backfill_twitter_source_data(dry_run=False, limit=None, batch_size=500):
    """Backfill source data for Twitter images without source field

    Args:
        dry_run: If True, only print what would be updated without making changes
        limit: Maximum number of documents to process (None for all)
        batch_size: Number of updates per batch write (max 500)
    """
    db = firestore.client()

    # Load media mapping
    print("Loading Twitter media mapping cache...")
    media_mapping = load_twitter_media_mapping()
    if not media_mapping:
        return

    print(f"Loaded {len(media_mapping)} media mappings")
    print()

    # Query for Twitter images without source data
    print("Querying Firestore for Twitter images without source data...")
    images_ref = db.collection('images')
    query = images_ref.where('type', '==', 'twitter')

    # Get all documents (we'll filter in memory for missing source field)
    docs = query.stream()

    updated_count = 0
    skipped_count = 0
    not_found_count = 0
    processed_count = 0
    batch_commit_count = 0

    # Batch write
    batch = db.batch()
    batch_update_count = 0

    for doc in docs:
        processed_count += 1
        doc_data = doc.to_dict()
        doc_id = doc.id

        # Check if source field already exists
        if 'source' in doc_data and doc_data['source']:
            skipped_count += 1
            if processed_count % 100 == 0:
                print(f"Progress: Processed {processed_count} docs, updated {updated_count}, skipped {skipped_count}, not found {not_found_count}")
            continue

        # Extract media ID from key
        s3_key = doc_data.get('key')
        if not s3_key:
            print(f"Warning: Document {doc_id} has no key field")
            skipped_count += 1
            continue

        media_id = extract_media_id_from_key(s3_key)
        if not media_id:
            print(f"Warning: Could not extract media ID from key: {s3_key}")
            skipped_count += 1
            continue

        # Look up source data
        if media_id not in media_mapping:
            not_found_count += 1
            if processed_count % 100 == 0:
                print(f"Progress: Processed {processed_count} docs, updated {updated_count}, skipped {skipped_count}, not found {not_found_count}")
            continue

        source_data = media_mapping[media_id]

        # Build source field
        source_field = {
            "tweetId": source_data.get("id_str"),
            "text": source_data.get("text"),
            "createdAt": source_data.get("created_at"),
            "mediaUrl": source_data.get("media_url"),
        }

        # Add user information if available
        if "user" in source_data:
            source_field["user"] = source_data["user"]

        # Add retweet information if available
        if "retweeted_status" in source_data:
            source_field["retweetedStatus"] = source_data["retweeted_status"]

        # Add quote status flag if available
        if source_data.get("is_quote_status"):
            source_field["isQuoteStatus"] = True

        # Update or print
        if dry_run:
            print(f"[DRY RUN] Would update {doc_id}:")
            print(f"  Media ID: {media_id}")
            print(f"  Tweet ID: {source_data.get('id_str')}")
            print(f"  User: @{source_data.get('user', {}).get('screen_name', 'unknown')}")
        else:
            # Add to batch
            batch.update(doc.reference, {"source": source_field})
            batch_update_count += 1

            if processed_count % 100 == 0:
                print(f"Queued {doc_id}: Tweet {source_data.get('id_str')} by @{source_data.get('user', {}).get('screen_name', 'unknown')}")

            # Commit batch when it reaches batch_size
            if batch_update_count >= batch_size:
                print(f"Committing batch of {batch_update_count} updates...")
                batch.commit()
                batch_commit_count += 1
                print(f"Batch #{batch_commit_count} committed successfully")

                # Reset batch
                batch = db.batch()
                batch_update_count = 0

        updated_count += 1

        # Check limit
        if limit and updated_count >= limit:
            print(f"\nReached limit of {limit} updates")
            break

        # Progress update
        if processed_count % 100 == 0:
            print(f"Progress: Processed {processed_count} docs, updated {updated_count}, skipped {skipped_count}, not found {not_found_count}")

    # Commit any remaining updates in the batch
    if not dry_run and batch_update_count > 0:
        print(f"\nCommitting final batch of {batch_update_count} updates...")
        batch.commit()
        batch_commit_count += 1
        print(f"Final batch #{batch_commit_count} committed successfully")

    print()
    print("=" * 70)
    print("Backfill complete!")
    print(f"Total documents processed: {processed_count}")
    print(f"Documents updated: {updated_count}")
    print(f"Documents skipped (already have source): {skipped_count}")
    print(f"Documents not found in mapping: {not_found_count}")
    if not dry_run:
        print(f"Total batches committed: {batch_commit_count}")
    print("=" * 70)


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="Backfill Twitter source data to Firestore images collection"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be updated without making changes"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of documents to update (default: all)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of updates per batch write (max 500, default: 500)"
    )

    args = parser.parse_args()

    if args.dry_run:
        print("=" * 70)
        print("DRY RUN MODE - No changes will be made to Firestore")
        print("=" * 70)
        print()

    backfill_twitter_source_data(dry_run=args.dry_run, limit=args.limit, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
