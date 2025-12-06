# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Firebase-based ML image classification system that:
1. Fetches daily rankings from Pixiv, Danbooru, and Gelbooru
2. Downloads images to Cloud Storage
3. Runs ML inference to predict user preference (not_bookmarked, bookmarked_public, bookmarked_private)
4. Provides a public web viewer to browse and filter VLM-captioned images

## Architecture

The project consists of three main components:

### Publisher (TypeScript - `publisher/`)
Scheduled functions that fetch image rankings and queue downloads:
- `fetchPixivDailyRankings` - Fetches Pixiv rankings daily at 15:00 JST
- `fetchDanbooruDailyRankings` - Fetches Danbooru popular posts daily
- `fetchGelbooruDailyImages` - Fetches Gelbooru images daily
- Task queue handlers (`downloadPixivImage`, `downloadDanbooruImage`, `downloadGelbooruImage`) that download images to Cloud Storage and create Firestore documents with `status: 'pending'`
- `updateModerationStats` - Firestore trigger that maintains moderation statistics per provider (count and sum) in the `moderationStats` collection

### Worker (Python - `worker/`)
ML inference and image processing functions:
- `onImageCreated` - Batches pending images (waits for 100+), downloads from Storage, runs inference
- Uses DeepDanbooru ResNet50 model to extract 6000 tag probabilities from images
- Runs three preference classifiers on tag probabilities:
  - sklearn LinearSVC
  - sklearn AdaBoost
  - PyTorch shallow network (6000→512→128→128→3)
- Updates Firestore documents with `topTagProbs` and `inferences`
- `vlm_captioner.py` - VLM-based captioning, moderation, age estimation, and tagging:
  - Supports multiple models: MiniCPM, JoyCaption, PixAI Tagger
  - PixAI Tagger v0.9: Generates ~13.5k Danbooru-style tags with confidence levels
    - Feature tags: high (≥0.35), medium (≥0.25), low (≥0.1), raw_scores (≥0.05)
    - Character tags: high (≥0.9), medium (≥0.8), low (≥0.5), raw_scores (≥0.2)
    - IP (copyright) tags: automatically extracted from character tags
    - Model: EVA02-Large encoder (frozen) + classification head (13,461 tags)
    - Performance: ~0.7s/image on ROCm GPU

### Public Website (Vue 3 + TypeScript - `public/`)
Web application for browsing VLM-captioned images:
- Built with Vue 3, TypeScript, Vite, and Tailwind CSS
- Firebase Authentication (Google Sign-In required)
- Features:
  - Browse images with VLM captions (JoyCaption and MiniCPM)
  - Sort by moderation rating, age estimation, or creation date (JoyCaption/MiniCPM/Qwen3 × High/Low)
  - Filter by rating range (provider + min/max) and age range (provider + min/max)
  - Page-based navigation (50 images per page with total page count from `moderationStats` collection)
  - Responsive sticky filter bar with integrated pagination
    - Mobile: Compact view with menu button + pagination, filters in modal overlay
    - Desktop: Full inline filter controls
  - Gallery mode with justified row layout and lightbox viewer
  - Favorites functionality with heart button (uncategorized by default)
  - Mobile-optimized layout (vertical stack on mobile, horizontal on desktop)
  - Click images to view detailed captions, age estimation, and metadata
  - Twitter source metadata display (tweet text, user, retweet info)
- Default sort: MiniCPM Created (Newest First)
- Deployed at: https://danbooru-ml-classifier.web.app

## Commands

### Publisher (TypeScript)
```bash
cd publisher
npm install
npm run build        # Compile TypeScript
npm run lint         # ESLint
npm run serve        # Build + start emulators
```

**Runtime**: Node.js 20

**Initial Data Population**:
```bash
# Initialize moderationStats collection from existing images data
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json \
npx ts-node scripts/init-moderation-stats.ts
```

### Worker (Python)
```bash
cd worker
python -m venv venv
venv/bin/pip install -r requirements.txt
```

**VLM Captioner**: Generates captions, moderation ratings, and age estimations
```bash
# Basic usage (MiniCPM only)
python vlm_captioner.py

# Specify models to run
python vlm_captioner.py --models minicpm joycaption
python vlm_captioner.py --models pixai

# Generate explanation for moderation ratings
python vlm_captioner.py --models minicpm --generate-explanation

# Backfill age estimation for existing images
python backfill_age_estimation.py --caption-model minicpm --age-model qwen3
```

**PixAI Tagger**: Generates Danbooru-style tags for images
```bash
# Tag images with PixAI Tagger v0.9
python vlm_captioner.py --models pixai

# Backfill PixAI tags for all existing images
python backfill_pixai_tags.py

# Backfill with options
python backfill_pixai_tags.py --max-images 100           # Limit number of images
python backfill_pixai_tags.py --no-skip-existing        # Reprocess existing tags
python backfill_pixai_tags.py --dry-run                 # Preview what would be processed

# Test single image
python test_pixai_tagger.py /path/to/image.jpg
```

**Prompts**: Stored in `worker/prompts/` directory
- `caption.txt` - Image captioning prompt
- `moderation.txt` - Moderation rating criteria
- `explanation.txt` - Moderation explanation generation
- `age_estimation.txt` - Image-based age estimation (deprecated)
- `age_estimation_from_caption.txt` - Caption-based age estimation (current)
- `age_estimation.schema.json` - Age estimation structured output schema
- `detailed_caption.txt` - Exhaustive image analysis with body part descriptions

### Public Website (Vue 3 + TypeScript)
```bash
cd public
npm install
npm run dev          # Start development server
npm run build        # Build for production
npm run lint         # Run Biome linter
npm run format       # Run Biome formatter
npm run check        # Run Biome lint + format
```

**IMPORTANT**: Always run `npm run lint` and `npm run format` (or `npm run check`) after modifying any code in the `public/` directory before committing or deploying.

### Deployment
```bash
firebase deploy --only functions           # Deploy all functions
firebase deploy --only functions:worker    # Deploy worker only
firebase deploy --only functions:publisher # Deploy publisher only
firebase deploy --only hosting             # Deploy public website only
firebase deploy --only firestore:rules     # Deploy Firestore security rules only
```

### Emulators
```bash
firebase emulators:start
# Firestore: localhost:8081
# Functions: localhost:5001
# Storage: localhost:9199
```

## Key Data Flow

1. Scheduled function fetches rankings → writes to `pixivRanking/`, `danbooruRanking/`, or `gelbooruImage/` collection
2. Firestore trigger queues download task
3. Task downloads image to `danbooru-ml-classifier-images` bucket, creates doc in `images/` with `status: 'pending'`
4. Worker function batches 100+ pending images, runs inference, updates status to `inferred`
5. VLM captioner processes images:
   - Generates captions (JoyCaption/MiniCPM)
   - Generates moderation ratings with explanations (0-10 scale)
   - Generates age estimations using caption-based inference with Qwen3-14B
   - Generates Danbooru-style tags (PixAI Tagger v0.9) with confidence levels and IP extraction
   - Loads Twitter source metadata from cache (if available)
   - Updates `images/` documents with all results
6. `updateModerationStats` function automatically maintains aggregated statistics (count, sum) per provider in `moderationStats/` collection
7. Public website queries `images/` collection with pagination (50 images per page) and displays total page count from `moderationStats/` collection

## Firestore Collections

### `images/`
Main collection storing image metadata and ML results:
- `status`: Image processing status (pending, inferred)
- `type`: Image source (pixiv, danbooru, gelbooru, twitter)
- `captions.[provider]`: VLM caption data with metadata
- `moderations.[provider]`: Moderation results with numeric rating (0-10 scale) and explanation
- `ageEstimations.[provider]`: Age estimation results with main_character_age (pre-calculated for queries), estimated_age_range, confidence_level, gender, reasoning, and metadata
- `tags.[provider]`: PixAI tagging results with tag_list (high/medium/low confidence × character/feature/ip), raw_scores, and metadata
- `twitterSource`: Twitter metadata (tweetId, text, user, retweetedTweet) if image is from Twitter
- `favorites`: User favorites data (isFavorited, categories array)
- `topTagProbs`: ML tag probabilities
- `inferences`: Preference classification results

### `moderationStats/`
Aggregated statistics per VLM provider (joycaption, minicpm):
- `count`: Total number of images with moderation ratings
- `sum`: Sum of all moderation ratings
- `updatedAt`: Last update timestamp
- Automatically maintained by `updateModerationStats` Firebase Function

### `pixivRanking/`, `danbooruRanking/`, `gelbooruImage/`
Source ranking data from external APIs.

## Firestore Security Rules

- `images/`:
  - Read access for authenticated user (hakatasiloving@gmail.com)
  - Write access to `favorites` field only for authenticated user with validation:
    - `isFavorited` must be boolean
    - `categories` must be array with max 50 items, each item max 100 chars
    - Data consistency: `isFavorited` must match `categories.size() > 0`
- `moderationStats/`: Read access for authenticated user (hakatasiloving@gmail.com)
- All other write operations: Firebase Functions only

## Firestore Indexes

The project uses 20+ composite indexes for efficient querying:
- Age estimation sorting + rating filtering combinations
- Rating sorting + age filtering combinations
- Supports queries on `main_character_age` field with multiple provider combinations
- See `firestore.indexes.json` for complete index definitions

## External Dependencies

- Firebase project: `danbooru-ml-classifier`
- Storage buckets: `danbooru-ml-classifier` (models), `danbooru-ml-classifier-images` (images)
- Required secrets: `PIXIV_SESSION_ID`, `DANBOORU_API_USER`, `DANBOORU_API_KEY`, `GELBOORU_API_USER`, `GELBOORU_API_KEY`
