"""
Delete images from Firestore and local filesystem starting from a specific file and all files after it.
Uses Firestore document's createdAt field (from captions or moderations metadata) as the deletion criterion.

Usage:
    python delete_images_after_timestamp.py --target-file ElQRAqLUYAAOKS-.jpg [--dry-run]
"""

import os
import argparse
from pathlib import Path
from urllib.parse import quote
from datetime import datetime
from firebase_admin import credentials, firestore, initialize_app

# Set up paths
SCRIPT_DIR = Path(__file__).parent
CRED_PATH = SCRIPT_DIR.parent / "danbooru-ml-classifier-firebase-adminsdk-uivsj-3a07a63db5.json"
LOCAL_IMAGE_DIR = Path.home() / "Images" / "hakataarchive" / "twitter"

# Initialize Firebase
if not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(CRED_PATH)

import firebase_admin
if not firebase_admin._apps:
    initialize_app()


def get_document_created_at(db, filename):
    """Get the createdAt timestamp from captions.minicpm.metadata.createdAt"""
    s3_key = f"twitter/{filename}"
    doc_id = quote(s3_key, safe='')
    doc_ref = db.collection('images').document(doc_id)
    doc = doc_ref.get()

    if not doc.exists:
        print(f"Warning: Document not found in Firestore: {doc_id}")
        return None

    data = doc.to_dict()

    # Check captions.minicpm.metadata.createdAt
    if 'captions' in data and 'minicpm' in data['captions']:
        caption_data = data['captions']['minicpm']
        if 'metadata' in caption_data and 'createdAt' in caption_data['metadata']:
            return caption_data['metadata']['createdAt']

    print(f"Warning: No captions.minicpm.metadata.createdAt found in document: {doc_id}")
    return None


def find_target_document_timestamp(db, target_filename):
    """Find the createdAt timestamp of the target file from Firestore"""
    target_created_at = get_document_created_at(db, target_filename)

    if target_created_at is None:
        return None

    print(f"Target file: {target_filename}")
    print(f"Firestore captions.minicpm.metadata.createdAt: {target_created_at}")

    return target_created_at


def get_documents_to_delete(db, target_created_at):
    """Get list of documents to delete using Firestore query (documents with createdAt >= target timestamp)"""
    # Query documents where captions.minicpm.metadata.createdAt >= target_created_at
    docs = db.collection('images').where(
        filter=firestore.FieldFilter('captions.minicpm.metadata.createdAt', '>=', target_created_at)
    ).stream()

    docs_to_delete = []

    for doc in docs:
        data = doc.to_dict()
        filename = data.get('key', '').split('/')[-1]
        if not filename:
            continue

        # Get createdAt
        created_at = None
        if 'captions' in data and 'minicpm' in data['captions']:
            caption_data = data['captions']['minicpm']
            if 'metadata' in caption_data and 'createdAt' in caption_data['metadata']:
                created_at = caption_data['metadata']['createdAt']

        if created_at is None:
            continue

        docs_to_delete.append({
            'doc_id': doc.id,
            'filename': filename,
            'created_at': created_at,
        })

    # Sort by createdAt (oldest first)
    docs_to_delete.sort(key=lambda d: d['created_at'])

    return docs_to_delete


def delete_from_firestore(db, file_path, dry_run=False):
    """Delete image document from Firestore"""
    image_name = file_path.name
    s3_key = f"twitter/{image_name}"
    doc_id = quote(s3_key, safe='')

    doc_ref = db.collection('images').document(doc_id)
    doc = doc_ref.get()

    if not doc.exists:
        print(f"  [Firestore] Document not found: {doc_id}")
        return False

    if dry_run:
        print(f"  [Firestore] Would delete: {doc_id}")
        return True
    else:
        doc_ref.delete()
        print(f"  [Firestore] Deleted: {doc_id}")
        return True


def delete_local_file(file_path, dry_run=False):
    """Delete local image file"""
    if dry_run:
        print(f"  [Local] Would delete: {file_path}")
        return True
    else:
        file_path.unlink()
        print(f"  [Local] Deleted: {file_path}")
        return True


def main():
    parser = argparse.ArgumentParser(
        description="Delete images from Firestore and local filesystem starting from a specific file"
    )
    parser.add_argument(
        "--target-file",
        required=True,
        help="Target filename to start deletion from (e.g., ElQRAqLUYAAOKS-.jpg)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without actually deleting"
    )

    args = parser.parse_args()

    print(f"Target file: {args.target_file}")
    print(f"Dry run: {args.dry_run}")
    print()

    # Initialize Firestore
    db = firestore.client()

    # Find target document timestamp from Firestore
    target_created_at = find_target_document_timestamp(db, args.target_file)
    if target_created_at is None:
        return

    print()

    # Get documents to delete
    print("Scanning Firestore documents...")
    docs_to_delete = get_documents_to_delete(db, target_created_at)

    if not docs_to_delete:
        print("No documents to delete")
        return

    print(f"Found {len(docs_to_delete)} documents to delete:")
    for i, doc_info in enumerate(docs_to_delete, 1):
        created_at_str = doc_info['created_at'].strftime('%Y-%m-%d %H:%M:%S UTC') if hasattr(doc_info['created_at'], 'strftime') else str(doc_info['created_at'])
        print(f"  {i:3d}. {doc_info['filename']} ({created_at_str})")

    print()

    # Confirm deletion (unless dry run)
    if not args.dry_run:
        response = input(f"Delete {len(docs_to_delete)} documents and files? (yes/no): ")
        if response.lower() != "yes":
            print("Cancelled")
            return

    print()

    # Delete documents and files
    deleted_firestore = 0
    deleted_local = 0

    for i, doc_info in enumerate(docs_to_delete, 1):
        print(f"[{i}/{len(docs_to_delete)}] {doc_info['filename']}")

        # Delete from Firestore
        if args.dry_run:
            print(f"  [Firestore] Would delete: {doc_info['doc_id']}")
            deleted_firestore += 1
        else:
            doc_ref = db.collection('images').document(doc_info['doc_id'])
            doc_ref.delete()
            print(f"  [Firestore] Deleted: {doc_info['doc_id']}")
            deleted_firestore += 1

        # Delete local file
        local_path = LOCAL_IMAGE_DIR / doc_info['filename']
        if local_path.exists():
            if delete_local_file(local_path, dry_run=args.dry_run):
                deleted_local += 1
        else:
            print(f"  [Local] File not found: {local_path}")

    print()
    print(f"Summary:")
    if args.dry_run:
        print(f"  Would delete from Firestore: {deleted_firestore}")
        print(f"  Would delete local files: {deleted_local}")
    else:
        print(f"  Deleted from Firestore: {deleted_firestore}")
        print(f"  Deleted local files: {deleted_local}")


if __name__ == "__main__":
    main()
