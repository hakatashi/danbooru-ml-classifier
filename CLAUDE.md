# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a hybrid ML image classification system that:
1. Fetches daily rankings from Pixiv, Danbooru, and Gelbooru (local cron job)
2. Downloads images to local disk (`/mnt/cache/danbooru-ml-classifier/images`)
3. Stores image metadata in local MongoDB
4. Runs ML inference to predict user preference (not_bookmarked, bookmarked_public, bookmarked_private)
5. Trains PU Learning-based preference classifiers using extracted image features
6. Provides a public web viewer to browse and filter VLM-captioned images

## Architecture

The project consists of four main components:

### Publisher (TypeScript - `publisher/`)
Split into two parts:

**Local cron job** (`src/cron.ts`) — runs on local machine, no Firebase required:
- `fetchPixivDailyRankings` - Fetches Pixiv rankings and downloads images
- `fetchDanbooruDailyRankings` - Fetches Danbooru popular posts and downloads images
- `fetchGelbooruDailyImages` - Fetches Gelbooru images and downloads images
- Images saved to `IMAGE_CACHE_DIR` (default: `/mnt/cache/danbooru-ml-classifier/images`)
- Metadata stored in local MongoDB collections: `images`, `pixivRanking`, `danbooruRanking`, `gelbooruImage`, `sankakuImage`, `pixivPages`

**Firebase Functions** (`src/index.ts`) — still deployed to Firebase:
- `updateModerationStats` - Firestore trigger that maintains moderation statistics per provider (count and sum) in the `moderationStats` collection
- API functions and novel generator (see `src/api.ts`, `src/novel-generator.ts`)

### Worker (Python - `worker/`)
ML inference and image processing functions:
- `main.py` - Local batch job that processes pending images in MongoDB and saves ML scores:
  - Queries MongoDB `images` collection for `status='pending'` documents with a `localPath` that exists on disk
  - Extracts three feature types per image:
    - DeepDanbooru ResNet50: 6000-dim tag probability vector
    - EVA02-Large encoder: 1024-dim visual embedding
    - PixAI Tagger v0.9: 13461-dim tag probability vector
  - Runs all models from `pu-learning/data/models/`:
    - Legacy sklearn multiclass models (`sklearn-multiclass-*.joblib`) → `{not_bookmarked, bookmarked_public, bookmarked_private}`
    - Legacy PyTorch shallow network (`torch-multiclass-onehot-shallow-network-multilayer`) → same three-class scores
    - PU Learning models (`{feature}_{label}_{method}.joblib`) → `{score: float}`
  - Updates MongoDB documents with `inferences` (keyed by model filename) and `importantTagProbs`
  - `importantTagProbs` stores the top-50 important tags from two feature importance CSVs:
    - `deepdanbooru`: from `feature_importance_deepdanbooru_pixiv_private_elkan_noto_positive.csv`
    - `pixai`: from `feature_importance_pixai_pixiv_private_nnpu_positive.csv`
- `vlm_captioner.py` - VLM-based captioning, moderation, age estimation, and tagging:
  - Supports multiple models: MiniCPM, JoyCaption, PixAI Tagger
  - PixAI Tagger v0.9: Generates ~13.5k Danbooru-style tags with confidence levels
    - Feature tags: high (≥0.35), medium (≥0.25), low (≥0.1), raw_scores (≥0.05)
    - Character tags: high (≥0.9), medium (≥0.8), low (≥0.5), raw_scores (≥0.2)
    - IP (copyright) tags: automatically extracted from character tags
    - Model: EVA02-Large encoder (frozen) + classification head (13,461 tags)
    - Performance: ~0.7s/image on ROCm GPU

### PU Learning (Python - `pu-learning/`)
PU Learning-based preference classifier for predicting image preference:
- `scripts/extract_features.py` - Extracts three feature types from images with HDF5 storage and resumable processing:
  - `deepdanbooru` - 6000-dim tag probability vector (ResNet50)
  - `eva02` - 1024-dim visual embedding (EVA02-Large encoder)
  - `pixai` - 13461-dim tag probability vector (PixAI Tagger v0.9)
  - EVA02 and PixAI share a single forward pass
- `scripts/build_dataset.py` - Assigns train/val/test splits (stratified by label) and verifies features are extracted
  - Positive labels: pixiv_public, pixiv_private, twitter (70/15/15% split)
  - Unlabeled: 90/5/5% split
- `scripts/train_pu.py` - Trains 27 PU Learning models (3 labels × 3 methods × 3 feature types):
  - Methods: `elkan_noto` (EM-based), `biased_svm` (asymmetric weights), `nnpu` (non-negative PU risk, GPU)
  - Outputs: `data/models/{feature}_{label}_{method}.joblib` and `data/results/metrics.csv`
  - Supports `--workers N` for parallel training via ProcessPoolExecutor
  - Default: skips already-trained models; use `--overwrite` to force retraining
- `scripts/score_unlabeled.py` - Scores test-split unlabeled images using trained classifiers, computes binary AUC-ROC, saves top-K montage PNGs to `data/results/`
  - Supports `--features/--labels/--methods` flags to filter which PU models to evaluate
- `scripts/feature_importance.py` - Extracts tag-level feature importance for trained PU models:
  - Linear coefficients from `elkan_noto`/`biased_svm` models
  - Mean signed input gradients from `nnpu` models
  - Supports `deepdanbooru` and `pixai` features (eva02 excluded — no tag labels)
  - Outputs per-model CSVs and a combined `feature_importance_all.csv`
- `scripts/visualize_attribution.py` - Per-image attribution visualizations:
  - `tag_contribution` mode: image + bar chart of top contributing tags (deepdanbooru/pixai)
  - `gradcam` mode: GradCAM heatmap over EVA02 last ViT block for eva02 models
  - Supports `--top-k`/`--bottom-k` for highest/lowest scored images; `--mode all` for both
- `scripts/build_eval_dataset.py` - Builds evaluation set from `manual_labels.json`:
  - SHA-256 deduplication (within eval and against training splits)
  - Extracts deepdanbooru/eva02/pixai features into HDF5
  - Saves manifest to `data/metadata/eval_manifest.parquet`
- `scripts/eval_models.py` - Evaluates legacy multiclass and PU Learning models on the eval set:
  - Metrics: weighted NDCG@K, AUC-ROC, and AP with graded relevance scoring
- `reports/model_evaluation_report.md` - Summary of PU Learning model performance across feature types, methods, and labels
- `scripts/config.py` - Shared configuration (paths, dimensions, constants)
- `notebooks/` - Jupyter notebooks for sklearn and PyTorch classifier experiments

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
npm run test         # Run unit tests (Vitest)
npm run serve        # Build + start emulators (Firebase Functions only)
```

**Runtime**: Node.js 20

**Local cron job** (fetch rankings + download images to `/mnt/cache`):
```bash
cd publisher

# Run scheduler (daily at 15:00 Asia/Tokyo)
npm run cron

# Run a specific job immediately (builds first)
npm run fetch:all
npm run fetch:pixiv
npm run fetch:danbooru
npm run fetch:gelbooru
npm run fetch:sankaku
```

**Systemd service** (for production daily automation):
```bash
# Install systemd user service/timer
cd publisher/systemd
./install.sh    # Copies units to ~/.config/systemd/user/, enables timer

# Files:
# danbooru-fetch.service - oneshot service running `npm run fetch:all`
# danbooru-fetch.timer   - fires daily at 15:00 JST (Asia/Tokyo, Persistent=true)
```

**Environment variables** for local cron (can be set in `publisher/.env`):
- `IMAGE_CACHE_DIR` - Directory to save downloaded images (default: `/mnt/cache/danbooru-ml-classifier/images`)
- `MONGODB_URI` - MongoDB connection URI (default: `mongodb://localhost:27017`)
- `MONGODB_DB` - MongoDB database name (default: `danbooru-ml-classifier`)
- `PIXIV_SESSION_ID` - Pixiv session cookie
- `DANBOORU_API_USER` / `DANBOORU_API_KEY` - Danbooru API credentials
- `GELBOORU_API_USER` / `GELBOORU_API_KEY` - Gelbooru API credentials
- `SANKAKU_USERNAME` / `SANKAKU_PASSWORD` - Sankaku Complex account credentials
- `SANKAKU_CRAWL_DEFAULT_PAGES` - Number of pages to fetch from Sankaku popularity ranking (default: 20)
- `SANKAKU_CRAWL_ADDITIONAL_TAGS` - Space-separated extra tags for additional 2-page crawls (e.g. specific artists)

**Firestore → MongoDB migration**:
```bash
# Import all Firestore collections to local MongoDB
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json \
npx ts-node --project publisher/tsconfig.json publisher/scripts/import-firestore.ts

# Import specific collections only
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json \
COLLECTIONS=images,pixivRanking \
npx ts-node --project publisher/tsconfig.json publisher/scripts/import-firestore.ts

# Options (env vars):
#   MONGODB_URI      - MongoDB URI (default: mongodb://localhost:27017)
#   MONGODB_DB       - Database name (default: danbooru-ml-classifier)
#   IMPORT_BATCH     - Firestore page size (default: 500)
#   COLLECTIONS      - Comma-separated list (default: images,pixivRanking,danbooruRanking,gelbooruImage)
```

### Worker (Python)
```bash
cd worker
python -m venv venv
venv/bin/pip install -r requirements.txt
```

**ML batch inference** (`main.py`): Scores pending images with all PU Learning and legacy models
```bash
cd worker
# Run batch inference on all pending images with local files
venv/bin/python main.py

# Environment variables (can be set in worker/.env or shell):
#   IMAGE_CACHE_DIR - Local image directory (default: /mnt/cache/danbooru-ml-classifier/images)
#   MONGODB_URI     - MongoDB URI (default: mongodb://localhost:27017)
#   MONGODB_DB      - Database name (default: danbooru-ml-classifier)
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

### PU Learning (Python)
```bash
cd pu-learning
bash setup.sh              # Create venv and install ROCm-compatible PyTorch + dependencies
```

**Feature extraction**:
```bash
cd pu-learning
# Extract all feature types
python scripts/extract_features.py

# Extract specific features (eva02 and pixai always run together)
python scripts/extract_features.py --features deepdanbooru
python scripts/extract_features.py --features eva02_pixai
python scripts/extract_features.py --batch-size 32
```

**Dataset preparation**:
```bash
python scripts/build_dataset.py              # Assign train/val/test splits
python scripts/build_dataset.py --check-features  # Also verify all features extracted
```

**Training**:
```bash
# Train all 27 models (3 labels × 3 methods × 3 feature types)
python scripts/train_pu.py

# Train specific combinations
python scripts/train_pu.py --features eva02 --methods biased_svm
python scripts/train_pu.py --labels pixiv_public twitter --features all --methods all

# Parallel training
python scripts/train_pu.py --workers 4

# GPU nnPU with custom epochs
python scripts/train_pu.py --features eva02 --methods nnpu --epochs 100

# Grid search
python scripts/train_pu.py --grid-search --features deepdanbooru

# Force retrain (default skips already-trained models)
python scripts/train_pu.py --overwrite
```

**Scoring**:
```bash
# Score test-split unlabeled images and save montage PNGs
python scripts/score_unlabeled.py
python scripts/score_unlabeled.py --top-k 20
python scripts/score_unlabeled.py --split val
python scripts/score_unlabeled.py --classes bookmarked_private bookmarked_public

# Filter which PU models to score
python scripts/score_unlabeled.py --features eva02 --labels pixiv_public --methods nnpu
```

**Feature importance**:
```bash
python scripts/feature_importance.py                          # All models
python scripts/feature_importance.py --features deepdanbooru  # Specific feature type
# Outputs: data/results/feature_importance_*.csv and feature_importance_all.csv
```

**Attribution visualization**:
```bash
python scripts/visualize_attribution.py --model deepdanbooru_pixiv_public_biased_svm
python scripts/visualize_attribution.py --mode gradcam --model eva02_pixiv_public_nnpu
python scripts/visualize_attribution.py --mode all --top-k 10 --bottom-k 5
```

**Model evaluation**:
```bash
# Build evaluation dataset from manual labels
python scripts/build_eval_dataset.py

# Evaluate models on eval set
python scripts/eval_models.py
# See reports/model_evaluation_report.md for results summary
```

**Manual labeling tool** (`labeler/`):
```bash
cd pu-learning
# Start labeling web UI (auto-builds frontend on first run)
source venv/bin/activate
python labeler/app.py              # → http://localhost:8765
python labeler/app.py --port 9000  # Custom port
python labeler/app.py --no-build   # Skip frontend build (faster for iteration)

# Labels are saved to: data/labels/manual_labels.json
# Images to label: DMC images NOT in splits.parquet (~8641 images)
# Labels: pixiv_public | pixiv_private | not_bookmarked
# Keyboard shortcuts: 1/Q=public, 2/W=private, 3/E=not_bm, S=skip, ←/→=navigate
# Frontend: Vite + React + TypeScript + CSS Modules (source in labeler/frontend/)
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

1. Local cron job (`src/cron.ts`) fetches rankings → saves to MongoDB (`pixivRanking`, `danbooruRanking`, `gelbooruImage`, `sankakuImage`)
2. Cron job downloads images to `IMAGE_CACHE_DIR`, creates doc in MongoDB `images` with `status: 'pending'`
3. Worker function batches 100+ pending images, runs inference, updates status to `inferred`
4. VLM captioner processes images:
   - Generates captions (JoyCaption/MiniCPM)
   - Generates moderation ratings with explanations (0-10 scale)
   - Generates age estimations using caption-based inference with Qwen3-14B
   - Generates Danbooru-style tags (PixAI Tagger v0.9) with confidence levels and IP extraction
   - Loads Twitter source metadata from cache (if available)
   - Updates `images` documents (MongoDB) with all results
5. `updateModerationStats` Firebase Function automatically maintains aggregated statistics (count, sum) per provider in `moderationStats/` Firestore collection
6. Public website queries Firestore `images/` collection with pagination (50 images per page) and displays total page count from `moderationStats/` collection

## MongoDB Collections (local)

### `images`
Main collection storing image metadata and ML results (mirrors Firestore `images/`):
- `status`: Image processing status (pending, inferred)
- `type`: Image source (pixiv, danbooru, gelbooru, twitter)
- `captions.[provider]`: VLM caption data with metadata
- `moderations.[provider]`: Moderation results with numeric rating (0-10 scale) and explanation
- `ageEstimations.[provider]`: Age estimation results with main_character_age (pre-calculated for queries), estimated_age_range, confidence_level, gender, reasoning, and metadata
- `tags.[provider]`: PixAI tagging results with tag_list (high/medium/low confidence × character/feature/ip), raw_scores, and metadata
- `twitterSource`: Twitter metadata (tweetId, text, user, retweetedTweet) if image is from Twitter
- `favorites`: User favorites data (isFavorited, categories array)
- `importantTagProbs`: Top-50 important tags per feature type — `{deepdanbooru: {tag: prob}, pixai: {tag: prob}}`
- `inferences`: ML model scores keyed by model filename — PU models: `{score: float}`, legacy multiclass: `{not_bookmarked, bookmarked_public, bookmarked_private}`

### `pixivRanking`, `danbooruRanking`, `gelbooruImage`, `sankakuImage`
Source ranking data from external APIs. Document `_id` = Firestore document ID (string).

### `pixivPages`
Pixiv per-artwork page URL data. Document `_id` = Pixiv artwork ID.

## Firestore Collections (Firebase — used by public website and Firebase Functions)

### `images/`
Mirror of MongoDB `images` collection, updated by worker and VLM captioner.

### `moderationStats/`
Aggregated statistics per VLM provider (joycaption, minicpm):
- `count`: Total number of images with moderation ratings
- `sum`: Sum of all moderation ratings
- `updatedAt`: Last update timestamp
- Automatically maintained by `updateModerationStats` Firebase Function

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
- Storage buckets: `danbooru-ml-classifier` (models), `danbooru-ml-classifier-images` (images, legacy)
- Local MongoDB: `danbooru-ml-classifier` database (default: `mongodb://localhost:27017`)
- Local image cache: `/mnt/cache/danbooru-ml-classifier/images` (configurable via `IMAGE_CACHE_DIR`)
- Required secrets (publisher cron): `PIXIV_SESSION_ID`, `DANBOORU_API_USER`, `DANBOORU_API_KEY`, `GELBOORU_API_USER`, `GELBOORU_API_KEY`
