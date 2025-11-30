#!/usr/bin/env python3
"""
Build Twitter Media ID to Tweet Mapping Cache

Scans the DynamoDB hakataarchive-entries-twitter table and creates a JSON mapping
from Twitter media IDs (e.g., "FVYuylQagAU2WHq.jpg") to tweet metadata.

This allows vlm_captioner.py to efficiently look up source tweet data when processing images.
"""

import json
import boto3
from pathlib import Path
from urllib.parse import urlparse

# DynamoDB configuration
DYNAMODB_TABLE = "hakataarchive-entries-twitter"
DYNAMODB_REGION = "ap-northeast-1"

# Output cache file
CACHE_DIR = Path(__file__).parent.parent.parent / ".cache"
OUTPUT_FILE = CACHE_DIR / "twitter_media_mapping.json"


def extract_media_id_from_url(media_url):
    """Extract media ID from Twitter media URL

    Args:
        media_url: URL like "http://pbs.twimg.com/media/FVYuylQagAU2WHq.jpg"

    Returns:
        str: Media ID like "FVYuylQagAU2WHq.jpg", or None if invalid
    """
    if not media_url:
        return None

    # Parse URL and get the filename from path
    parsed = urlparse(media_url)
    path_parts = parsed.path.split('/')

    if len(path_parts) > 0:
        return path_parts[-1]  # Get the last part (filename)

    return None


def scan_dynamodb_table(limit=None):
    """Scan DynamoDB table and build media ID mapping

    Args:
        limit: Maximum number of items to scan (None for all items)

    Returns:
        dict: Mapping from media ID to tweet metadata
    """
    dynamodb = boto3.client('dynamodb', region_name=DYNAMODB_REGION)

    mapping = {}
    scan_kwargs = {
        'TableName': DYNAMODB_TABLE,
    }

    # Add limit if specified
    if limit:
        scan_kwargs['Limit'] = limit

    scanned_items = 0
    tweets_with_media = 0
    total_media_items = 0

    print(f"Scanning DynamoDB table: {DYNAMODB_TABLE}")
    if limit:
        print(f"Limited to first {limit} items")
    else:
        print("This may take several minutes...")

    while True:
        response = dynamodb.scan(**scan_kwargs)
        items = response.get('Items', [])

        for item in items:
            scanned_items += 1

            if scanned_items % 1000 == 0:
                print(f"Scanned {scanned_items} items, found {tweets_with_media} tweets with {total_media_items} media items...")

            # Extract tweet ID
            tweet_id = item.get('id_str', {}).get('S')
            if not tweet_id:
                continue

            # Check for media in extended_entities first (preferred), then entities
            media_list = None

            extended_entities = item.get('extended_entities', {}).get('M', {})
            if extended_entities:
                media_list = extended_entities.get('media', {}).get('L', [])

            if not media_list:
                entities = item.get('entities', {}).get('M', {})
                if entities:
                    media_list = entities.get('media', {}).get('L', [])

            if not media_list:
                continue

            has_media = False

            # Process each media item
            for media_item in media_list:
                media_map = media_item.get('M', {})

                # Get media URL (prefer https)
                media_url = media_map.get('media_url_https', {}).get('S')
                if not media_url:
                    media_url = media_map.get('media_url', {}).get('S')

                if not media_url:
                    continue

                # Extract media ID from URL
                media_id = extract_media_id_from_url(media_url)
                if not media_id:
                    continue

                has_media = True
                total_media_items += 1

                # Build tweet metadata
                # Try full_text first (new format), fallback to text (old format)
                text = item.get('full_text', {}).get('S') or item.get('text', {}).get('S', '')

                tweet_data = {
                    'id_str': tweet_id,
                    'text': text,
                    'created_at': item.get('created_at', {}).get('S', ''),
                    'media_url': media_url,
                }

                # Extract user information
                # Try nested user object first (old format)
                user = item.get('user', {}).get('M', {})
                if user:
                    tweet_data['user'] = {
                        'screen_name': user.get('screen_name', {}).get('S', ''),
                        'name': user.get('name', {}).get('S', ''),
                        'id_str': user.get('id_str', {}).get('S', ''),
                    }
                else:
                    # Fallback to user_id_str (new format)
                    # In this case, we don't have screen_name and name
                    user_id_str = item.get('user_id_str', {}).get('S')
                    if user_id_str:
                        tweet_data['user'] = {
                            'screen_name': '',
                            'name': '',
                            'id_str': user_id_str,
                        }

                # Extract retweet/quote information if present
                if item.get('is_quote_status', {}).get('BOOL'):
                    tweet_data['is_quote_status'] = True

                retweeted_status = item.get('retweeted_status', {}).get('M', {})
                if retweeted_status:
                    rt_user = retweeted_status.get('user', {}).get('M', {})
                    if rt_user:
                        tweet_data['retweeted_status'] = {
                            'id_str': retweeted_status.get('id_str', {}).get('S', ''),
                            'user': {
                                'screen_name': rt_user.get('screen_name', {}).get('S', ''),
                                'name': rt_user.get('name', {}).get('S', ''),
                            }
                        }

                # Store in mapping
                mapping[media_id] = tweet_data

            if has_media:
                tweets_with_media += 1

        # Check if there are more items to scan
        last_evaluated_key = response.get('LastEvaluatedKey')
        if not last_evaluated_key:
            break

        # Stop if we've reached the limit
        if limit and scanned_items >= limit:
            break

        scan_kwargs['ExclusiveStartKey'] = last_evaluated_key

    print(f"\nScan complete!")
    print(f"Total items scanned: {scanned_items}")
    print(f"Tweets with media: {tweets_with_media}")
    print(f"Total media items: {total_media_items}")
    print(f"Unique media IDs: {len(mapping)}")

    return mapping


def main():
    """Main function to build and save media mapping cache"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Build Twitter media ID to tweet mapping cache from DynamoDB"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of items to scan (default: all items)"
    )

    args = parser.parse_args()

    # Create cache directory if it doesn't exist
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Scan DynamoDB and build mapping
    mapping = scan_dynamodb_table(limit=args.limit)

    # Save to JSON file
    print(f"\nSaving mapping to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

    print(f"Mapping cache saved successfully!")
    print(f"File size: {OUTPUT_FILE.stat().st_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
