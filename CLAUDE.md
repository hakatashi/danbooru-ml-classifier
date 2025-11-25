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
ML inference function triggered by Firestore document creation:
- `onImageCreated` - Batches pending images (waits for 100+), downloads from Storage, runs inference
- Uses DeepDanbooru ResNet50 model to extract 6000 tag probabilities from images
- Runs three preference classifiers on tag probabilities:
  - sklearn LinearSVC
  - sklearn AdaBoost
  - PyTorch shallow network (6000→512→128→128→3)
- Updates Firestore documents with `topTagProbs` and `inferences`

### Public Website (Vue 3 + TypeScript - `public/`)
Web application for browsing VLM-captioned images:
- Built with Vue 3, TypeScript, Vite, and Tailwind CSS
- Firebase Authentication (Google Sign-In required)
- Features:
  - Browse images with VLM captions (JoyCaption and MiniCPM)
  - Sort by moderation rating (high to low or low to high) or creation date
  - Page-based navigation (20 images per page with total page count from `moderationStats` collection)
  - Responsive sticky filter bar with integrated pagination
  - Mobile-optimized layout (vertical stack on mobile, horizontal on desktop)
  - Click images to view detailed captions and metadata
- Default sort: MiniCPM Rating (High to Low)
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
5. VLM captioner updates `images/` documents with captions and moderation ratings in `moderations.[provider].result` field
6. `updateModerationStats` function automatically maintains aggregated statistics (count, sum) per provider in `moderationStats/` collection
7. Public website queries `images/` collection with pagination (20 images per page) and displays total page count from `moderationStats/` collection

## Firestore Collections

### `images/`
Main collection storing image metadata and ML results:
- `status`: Image processing status (pending, inferred)
- `type`: Image source (pixiv, danbooru, gelbooru)
- `captions.[provider]`: VLM caption data with metadata
- `moderations.[provider]`: Moderation results with numeric rating (0-10 scale)
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

- `images/`: Read access for authenticated user (hakatasiloving@gmail.com)
- `moderationStats/`: Read access for authenticated user (hakatasiloving@gmail.com)
- All write operations: Firebase Functions only

## External Dependencies

- Firebase project: `danbooru-ml-classifier`
- Storage buckets: `danbooru-ml-classifier` (models), `danbooru-ml-classifier-images` (images)
- Required secrets: `PIXIV_SESSION_ID`, `DANBOORU_API_USER`, `DANBOORU_API_KEY`, `GELBOORU_API_USER`, `GELBOORU_API_KEY`
