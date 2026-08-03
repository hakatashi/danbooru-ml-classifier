# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a hybrid ML image classification system that:
1. Fetches daily rankings from Pixiv, Danbooru, and Gelbooru (local cron job)
2. Downloads images to local disk (`/mnt/cache2/danbooru-ml-classifier/images`)
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
- Images saved to `IMAGE_CACHE_DIR` (default: `/mnt/cache2/danbooru-ml-classifier/images`)
- Metadata stored in local MongoDB collections: `images`, `pixivRanking`, `danbooruRanking`, `gelbooruImage`, `sankakuImage`, `pixivPages`

**Firebase Functions** (`src/index.ts`) — still deployed to Firebase:
- `updateModerationStats` - Firestore trigger that maintains moderation statistics per provider (count and sum) in the `moderationStats` collection
- API functions and novel generator (see `src/api.ts`, `src/novel-generator.ts`)

### Worker (Python - `worker/`)
ML inference and image processing functions:
- `main.py` - Local batch job that processes pending images in MongoDB and saves ML scores:
  - Queries MongoDB `images` collection for `status='pending'` documents with a `localPath` that exists on disk
  - **Not manually scheduled**: this machine's GPU-exclusive apps (including this job) are managed by a separate
    sibling project, `../HakataMatrix-app-controller-claude`, which runs `main.py` as its daily cronjob app at
    05:00 JST, stopping/resuming the resident GPU app (a Slackbot's llama-server) around it. There is deliberately
    no systemd timer for inference in this repo — adding one would race the controller and double-run/OOM the GPU.
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
  - After each run, recomputes the Virgo/Libra ensembles (see `ensembles.py`) for every date touched by that run, and writes deepdanbooru/eva02/pixai feature vectors to the HDF5 feature store (see `feature_store.py`) — both non-fatal (`log.warning` on failure, inference itself is unaffected)
- `ensembles.py` - Rank-average ensembles materialized into `inferences.<key>.score`:
  - `ensemble_virgo_v1` (♍) — rank-average of the 9 `pixiv_private` PU models (3 features × 3 methods); strongest within-page AUC on pages 0-1 (the pages actually browsed day to day)
  - `ensemble_libra_v1` (♎) — rank-average of 5 hand-picked top models across feature types (incl. one twitter-trained model); strongest within-page AUC on deeper pages
  - Both are pure re-aggregations of scores `main.py` already wrote (percentile rank per model within a day's population, then averaged) — no GPU, no new features. See `pu-learning/reports/recommendation_improvement_plan.md` sections 1.5-1.5.1 for why two ensembles ship instead of one (offline metrics disagree by page depth) and `compute_and_write_ensembles()` for the batch entry point
- `compute_ensembles.py` - CLI to (re)compute ensembles for a date range or `--all`; `--dry-run` to preview
- `feature_store.py` - Monthly-sharded HDF5 feature store (`{FEATURE_STORE_DIR}/{feature}/{YYYY-MM}.h5`, default `/mnt/cache2/danbooru-ml-classifier/features/`), same layout as `pu-learning/scripts/extract_features.py`'s `H5FeatureStore` so pu-learning's `build_dataset.py`/`train_pu.py` can read shards without modification. Keys are MongoDB `_id` hex strings. `write_features()` is idempotent (checks `existing_ids()` per shard) and returns which docs got a complete set of features so callers can set `images.features = {stored: true, shard: "YYYY-MM"}`
- `backfill_features.py` - One-off/targeted backfill of the feature store for images `main.py` already scored:
  - `--ids-file PATH` (one MongoDB `_id` per line, e.g. from `pu-learning/scripts/build_impressions.py --dump-ids`) or `--date-from/--date-to`
  - Refuses to start unless `rocm-smi` reports enough free VRAM (`--min-free-vram-mb`, default 4000) — this machine's GPU-exclusive resident app (see `main.py` above) must be stopped first; `--skip-vram-check` bypasses this on non-ROCm hosts
  - `--dry-run` to preview counts before touching the GPU
- `api.py` - Thin REST API server that exposes MongoDB image data to the public website:
  - Serves images sorted by `importantTagProbs` or `inferences` values, filtered by date
  - Deployed at: https://danbooru-api.matrix.hakatashi.com (persisted via systemd user service)
  - Runs locally on port 8766, exposed via Nginx reverse proxy with Let's Encrypt TLS
  - CORS allowed origins: `danbooru-ml-classifier.web.app`, `localhost:5173/4173`
  - Endpoints:
    - `GET /images` — paginated list sorted by ML score or tag probability
    - `GET /images/{id}` — single image by MongoDB `_id` (accepts both ObjectId and legacy string ids)
    - `GET /images/{id}/similar` — similar images via Qdrant embeddings
    - `GET /inference-models` — available model keys and their types
    - `GET /important-tags` — available tag names per feature type
    - `GET /daily-counts`, `GET /post-source` — daily image counts, source URL resolution
    - `GET /health` — health check
    - `GET /favorites`, `GET /favorites/categories`, `GET /favorites/pool`, `GET /favorites/random`, `POST /favorites/lookup`, `POST /favorites/update` — favorites CRUD (see below); require a Firebase ID token
    - `POST /page-views/mark`, `POST /page-views/unmark`, `GET /page-views` — Daily Recommendation "page viewed" bookkeeping (see `pageViews` collection below); require a Firebase ID token
    - `POST /image-views` — increments `images.views.{detail,zoom}Count` when a Detail page or zoom/fullscreen viewer is opened; require a Firebase ID token
  - `sort_field` parameter validated against allowlist patterns:
    - `inferences.<model_key>.(score|not_bookmarked|bookmarked_public|bookmarked_private)` (this also covers the Virgo/Libra ensemble keys, `ensemble_virgo_v1`/`ensemble_libra_v1`)
    - `importantTagProbs.(deepdanbooru|pixai).<tag>`
    - `/favorites` additionally accepts `favorites.favoritedAt`, `favorites.updatedAt`, `date`
  - **Auth**: `/images*` and other read endpoints are public/unauthenticated. `/favorites/*`, `/page-views*`, `/image-views` (reads included) require `Authorization: Bearer <Firebase ID token>` restricted to `hakatasiloving@gmail.com` (`require_admin` dependency, `ALLOWED_EMAIL` / `FIREBASE_CRED_PATH` env vars). A startup check (`_assert_protected_routes`, driven by `_PROTECTED_PREFIXES`) fails fast if any route under a protected prefix is missing this dependency.
  - CORS `allow_methods` includes `POST` (used by `/favorites/update`, `/favorites/lookup`, `/page-views/mark`, `/page-views/unmark`, `/image-views`)
  - Systemd service and Nginx config templates in `worker/systemd/`; install via `bash worker/systemd/install-api.sh`
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
- `scripts/build_impressions.py` - Builds a labeled impression dataset for evaluating/improving recommendations, merging two sources:
  - `explicit` — MongoDB `pageViews` marks (see below); every image on a marked page is a real impression, weight 1.0
  - `reconstructed` — inferred from favorites: for each date, images ranked by `--sort-field` (default: Gemini's field); any page containing ≥1 favorite is treated as viewed, with negatives weighted by a confidence ladder (`--weight-profile {ladder,flat,explicit-only}`) based on page depth/contiguity/lag from `favoritedAt`
  - Explicit wins over reconstructed for the same `(date, sort_field, page)`
  - Cleaning (default on, `--no-clean` to skip): drops negatives sharing `artworkId` with a favorite, and drops any image overlapping `eval_manifest.parquet` (positive or negative)
  - Outputs `data/metadata/impressions.parquet`; `--dump-ids PATH` writes one MongoDB `_id` per line for `worker/backfill_features.py --ids-file`
  - See `reports/recommendation_improvement_plan.md` for the full derivation
- `scripts/eval_impressions.py` - Evaluates every existing `inferences.*` model plus the Virgo/Libra ensembles (computed on the fly via `worker/ensembles.py`, or custom ones via `--ensemble name:model1,model2,...`) against `impressions.parquet`:
  - No feature extraction, no joblib loading, no GPU — reads MongoDB `inferences.*` directly
  - Primary metric: page-internal AUC (pooled Mann-Whitney within each `(date, sort_field, page)` group), reported overall and by page-depth band (p0-1/p0-4/p5-19/p20+) — the only metric immune to the range-restriction bias from the incumbent sort field dominating the impression population
  - Recall@K is reported for reference only, flagged `biased_by_incumbent` for the sort field the impressions were built against (its Recall@K is structurally inflated — see the script docstring)
  - Time-series holdout only (`--test-days`, default 30); no random-split option (would leak through preference drift/page correlation)
  - Outputs `data/results/impression_metrics.csv` (upsert by `(model, test_days)`)
- `reports/model_evaluation_report.md` - Summary of PU Learning model performance across feature types, methods, and labels
- `reports/recommendation_improvement_plan.md` - Plan for improving Daily Recommendation using accumulated favorites/impression data; background for `build_impressions.py`/`eval_impressions.py`/`worker/ensembles.py`
- `scripts/config.py` - Shared configuration (paths, dimensions, constants, MongoDB URI/DB, `DAILY_PAGE_SIZE`)
- `notebooks/` - Jupyter notebooks for sklearn and PyTorch classifier experiments

### Public Website (Vue 3 + TypeScript - `public/`)
Web application for browsing VLM-captioned images:
- Built with Vue 3, TypeScript, Vite, and Tailwind CSS
- Firebase Authentication (Google Sign-In required)
- Two parallel data paths: `/daily`, `/daily/image/:id`, `/favorites`, and `/gallery` read from the MongoDB-backed worker REST API (`src/api/mlApi.ts`); `/archives` and `/image/:id` read directly from Firestore `images/` (`src/composables/useImages.ts`)
- Routes (`src/router.ts`):
  - `/daily` — Daily Recommendation: MongoDB-backed gallery with named sort presets (Aries–Leo, plus ♍ Virgo/♎ Libra rank-average ensembles — see `worker/ensembles.py`), calendar date picker, per-model/tag score sorting; footer "Mark page as viewed" button records an explicit `pageViews` entry for the current `(date, sort, page)`
  - `/daily/image/:id` — Daily Recommendation image detail, with similar-image strips; opening the page or its zoom/fullscreen viewer records an `images.views.{detail,zoom}Count` hit via `POST /image-views`
  - `/daily/unviewed` — matrix of the top 3 pages × last 30 days on the Gemini sort, showing which `(date, page)` combinations are/aren't marked viewed yet; cells link into `/daily` at that exact date/sort/page
  - `/favorites` — manage favorited images: sort by favorited/updated date or any ML score, filter by source/date-range/category, group by category, single and bulk category editing, lightbox with prev/next
  - `/gallery` — random full-screen viewer over favorited images (original, not thumbnail, URLs); no-repeat shuffle per session (`sessionStorage`), prefetches upcoming originals, keyboard (←/→/Space/F/D/Esc) and touch-swipe navigation; zooming in or entering fullscreen also records an `images.views.zoomCount` hit
  - `/archives` — Firestore-backed gallery/grid view with rating/age/PixAI-tag filters (formerly at `/gallery`, renamed when `/gallery` was repurposed for the random viewer — there is intentionally no redirect between the two)
  - `/image/:id(.*)` — Firestore-backed image detail
  - `/novels`, `/novels/:novelId` — generated novel list/detail
- Favorites (`src/composables/useFavorites.ts`, `src/components/FavoriteButton.vue`): backed by MongoDB `images.favorites` via the worker API's authenticated `/favorites/*` endpoints (Firebase ID token). `hydrateFromImages()` reads `favorites` embedded in API responses for free; `loadFavoritesForImages()` calls `POST /favorites/lookup` for the Firestore-backed views where it isn't embedded. Categories default to `Uncategorized`.
- Page views (`src/composables/usePageViews.ts`, `src/components/PageViewedButton.vue`): backed by MongoDB `pageViews`/`images.views` via the worker API's authenticated `/page-views/*` and `/image-views` endpoints. `markViewed()`/`unmarkViewed()` mirror the favorites composable's optimistic-update pattern; `recordView(imageId, kind)` is a fire-and-forget wrapper around `POST /image-views` used for automatic detail/zoom tracking. This is a personal-use app, so viewing is tracked via explicit marks and these automatic detail/zoom signals rather than passive impression logging.
- Shared gallery layout: `src/components/JustifiedGallery.vue` (justified-row + mobile grid, used by `/favorites`; `/daily` and `/archives` still have their own inline copies) and `src/composables/useGallery.ts`
- Image URLs (`src/api/mlApi.ts` `getImageUrl`): `https://matrix-images.hakatashi.com/danbooru-ml-classifier/{images,thumbnails}/{key}` for current-scheme images; legacy Firestore-era Twitter imports (`type: 'twitter'` with a non-ObjectId `_id`) resolve to `https://matrix-images.hakatashi.com/hakataarchive/twitter/{basename}` instead (no thumbnail variant exists for those)
- Other features:
  - Browse images with VLM captions (JoyCaption and MiniCPM)
  - Sort by moderation rating, age estimation, or creation date (JoyCaption/MiniCPM/Qwen3 × High/Low)
  - Filter by rating range (provider + min/max) and age range (provider + min/max)
  - Page-based navigation (50 images per page on `/archives`, with total page count from `moderationStats` collection)
  - Responsive sticky filter bar with integrated pagination
    - Mobile: Compact view with menu button + pagination, filters in modal overlay
    - Desktop: Full inline filter controls
  - Mobile-optimized layout (vertical stack on mobile, horizontal on desktop)
  - Click images to view detailed captions, age estimation, and metadata
  - Twitter source metadata display (tweet text, user, retweet info)
- Default sort: MiniCPM Created (Newest First) on `/archives`
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

**Local cron job** (fetch rankings + download images to `/mnt/cache2`):
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
- `IMAGE_CACHE_DIR` - Directory to save downloaded images (default: `/mnt/cache2/danbooru-ml-classifier/images`)
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

**MongoDB index management**:
```bash
cd publisher
npx ts-node --project tsconfig.json scripts/ensure-indexes.ts [--dry-run]
```

**Favorites migration** (one-shot, Firestore `favorites/` → MongoDB `images.favorites`):
```bash
cd publisher
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json \
npx ts-node --project tsconfig.json scripts/migrate-favorites-to-mongo.ts [--dry-run] [--prune] [--force]

# --dry-run  Print the report without writing anything
# --prune    Remove MongoDB favorites subdocs with no Firestore counterpart
# --force    Skip the safety guard that aborts if MongoDB's favorites are
#            already newer than Firestore's (i.e. the app has cut over)
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
#   IMAGE_CACHE_DIR - Local image directory (default: /mnt/cache2/danbooru-ml-classifier/images)
#   MONGODB_URI     - MongoDB URI (default: mongodb://localhost:27017)
#   MONGODB_DB      - Database name (default: danbooru-ml-classifier)
```

**Ensembles** (`ensembles.py` / `compute_ensembles.py`): (Re)compute the Virgo/Libra rank-average ensembles. No GPU — pure re-aggregation of scores `main.py` already wrote.
```bash
cd worker
venv/bin/python compute_ensembles.py --date-from 2026-07-01 --date-to 2026-08-04
venv/bin/python compute_ensembles.py --all                    # every date with inferred docs
venv/bin/python compute_ensembles.py --all --dry-run           # preview counts only
venv/bin/python compute_ensembles.py --date-from 2026-08-01 --date-to 2026-08-04 \
  --ensembles ensemble_virgo_v1                                # single ensemble
```

**Feature store backfill** (`backfill_features.py`): Persists deepdanbooru/eva02/pixai vectors for already-inferred images into the HDF5 feature store. GPU-heavy — confirm with the user and stop the resident GPU app first (see External Dependencies).
```bash
cd worker
venv/bin/python backfill_features.py --ids-file /path/to/ids.txt --dry-run   # preview
venv/bin/python backfill_features.py --ids-file /path/to/ids.txt             # e.g. from
  # pu-learning/scripts/build_impressions.py --dump-ids
venv/bin/python backfill_features.py --date-from 2026-04-10 --date-to 2026-08-03
```

**API server** (`api.py`): REST API exposing ML scores and tag probabilities to the public website
```bash
cd worker
# Start API server (development)
venv/bin/uvicorn api:app --host 127.0.0.1 --port 8766

# Install as systemd service + configure Nginx (first time setup)
bash systemd/install-api.sh

# Manage systemd service
systemctl --user status danbooru-ml-api.service
systemctl --user restart danbooru-ml-api.service
systemctl --user logs -f danbooru-ml-api.service

# Environment variables (same as main.py):
#   MONGODB_URI - MongoDB URI (default: mongodb://localhost:27017)
#   MONGODB_DB  - Database name (default: danbooru-ml-classifier)
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

**Impression-based evaluation** (favorites + explicit "page viewed" marks, no GPU):
```bash
cd pu-learning
venv/bin/python scripts/build_impressions.py                       # data/metadata/impressions.parquet
venv/bin/python scripts/build_impressions.py --weight-profile flat # ablation: no confidence downweighting
venv/bin/python scripts/build_impressions.py --dump-ids /tmp/viewed_ids.txt  # for backfill_features.py --ids-file

venv/bin/python scripts/eval_impressions.py                        # data/results/impression_metrics.csv
venv/bin/python scripts/eval_impressions.py --test-days 14
venv/bin/python scripts/eval_impressions.py \
  --ensemble my_combo:eva02_pixiv_private_nnpu_joblib,pixai_pixiv_private_elkan_noto_joblib
# See reports/recommendation_improvement_plan.md for background/derivation
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
3. Worker function batches 100+ pending images, runs inference, updates status to `inferred`, recomputes the Virgo/Libra ensembles for touched dates, and persists feature vectors to the HDF5 feature store
4. VLM captioner processes images:
   - Generates captions (JoyCaption/MiniCPM)
   - Generates moderation ratings with explanations (0-10 scale)
   - Generates age estimations using caption-based inference with Qwen3-14B
   - Generates Danbooru-style tags (PixAI Tagger v0.9) with confidence levels and IP extraction
   - Loads Twitter source metadata from cache (if available)
   - Updates `images` documents (MongoDB) with all results
5. `updateModerationStats` Firebase Function automatically maintains aggregated statistics (count, sum) per provider in `moderationStats/` Firestore collection
6. Public website's `/daily`, `/favorites`, and `/gallery` query the MongoDB-backed worker REST API (`worker/api.py`) directly; `/archives` and `/image/:id` still query Firestore `images/` with pagination (50 images per page) and display total page count from `moderationStats/` collection

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
- `favorites`: `{isFavorited: bool, categories: string[], favoritedAt: Date | null, updatedAt: Date}` — canonical source of truth for favorites (migrated from the Firestore `favorites/` collection; see `publisher/scripts/migrate-favorites-to-mongo.ts`). `favoritedAt` is set once on the 0→1 transition and preserved across later category edits, so "newest favorited" sorts correctly. `isFavorited === (categories.length > 0)`. Written only via `worker/api.py`'s authenticated `POST /favorites/update`.
- `importantTagProbs`: Top-50 important tags per feature type — `{deepdanbooru: {tag: prob}, pixai: {tag: prob}}`
- `inferences`: ML model scores keyed by model filename — PU models: `{score: float}`, legacy multiclass: `{not_bookmarked, bookmarked_public, bookmarked_private}`. Also holds the Virgo/Libra ensemble scores under `ensemble_virgo_v1`/`ensemble_libra_v1` (same `{score: float}` shape), written by `worker/ensembles.py`/`compute_ensembles.py`.
- `views`: `{detailCount, detailLastAt, zoomCount, zoomLastAt, firstViewedAt}` — automatic intermediate-label counters, incremented by `worker/api.py`'s `POST /image-views` when the Daily image Detail page or a zoom/fullscreen viewer is opened. `firstViewedAt` is set once via a MongoDB `$min` update (only writes if absent).
- `features`: `{stored: bool, shard: "YYYY-MM"}` — pointer into the HDF5 feature store (`worker/feature_store.py`); set once all three feature vectors (deepdanbooru/eva02/pixai) are persisted for that image.

### `pixivRanking`, `danbooruRanking`, `gelbooruImage`, `sankakuImage`
Source ranking data from external APIs. Document `_id` = Firestore document ID (string).

### `pixivPages`
Pixiv per-artwork page URL data. Document `_id` = Pixiv artwork ID.

### `pageViews`
Explicit "this Daily Recommendation page has been viewed" marks (personal-use app, so viewing is tracked by explicit user action rather than passive impression logging):
- `date`, `sortField`, `page`: identify the marked page (matches `/daily`'s URL query params)
- `imageIds`: ObjectId[] snapshot of exactly which images were on the page at mark time — day-level rankings can shift later (new images inferred, new models added), so this snapshot is what makes a mark usable as a training label after the fact
- `markedAt`: Date
- Unique on `(date, sortField, page)`; written only via `worker/api.py`'s authenticated `POST /page-views/mark` / `POST /page-views/unmark`
- Consumed by `pu-learning/scripts/build_impressions.py` as the `explicit` label source (see PU Learning section) and by the `/daily/unviewed` page

## MongoDB Indexes

- `images`: `favorites_favoritedAt_desc` — `{"favorites.favoritedAt": -1}`, partial index on `{"favorites.isFavorited": true}`. Makes every `/favorites*` query an IXSCAN over the (small) favorited set instead of a full collection scan; deliberately the *only* extra index on `images` — sorting by an arbitrary `inferences.*`/`importantTagProbs.*` field is done in-process in `worker/api.py` over the pre-filtered favorited set rather than indexed, since there are 100+ possible sort fields.
- `pageViews`: `pageViews_date_sortField_page_unique` — unique `{date: 1, sortField: 1, page: 1}`; `pageViews_sortField_date_desc` — `{sortField: 1, date: -1}` for the `/daily/unviewed` listing query.
- Managed by `publisher/scripts/ensure-indexes.ts` (idempotent; run manually or called from the favorites migration script), grouped by collection via each `IndexSpec`'s `collection` field. There is no automatic index creation at API startup.

## Firestore Collections (Firebase — used by public website and Firebase Functions)

### `images/`
Mirror of MongoDB `images` collection, updated by worker and VLM captioner. Still read directly by the `/archives` and `/image/:id` views.

### `favorites/`
Legacy, read-only. Favorites now live in MongoDB `images.favorites` (see above); this collection is preserved un-migrated as a rollback reference and is no longer written to by the app.

### `moderationStats/`
Aggregated statistics per VLM provider (joycaption, minicpm):
- `count`: Total number of images with moderation ratings
- `sum`: Sum of all moderation ratings
- `updatedAt`: Last update timestamp
- Automatically maintained by `updateModerationStats` Firebase Function

## Firestore Security Rules

- `images/`: Read access for authenticated user (hakatasiloving@gmail.com); all writes disallowed (`allow write: if false`)
- `favorites/`: Read-only for authenticated user (hakatasiloving@gmail.com); all writes disallowed — kept for historical reference, superseded by MongoDB `images.favorites`
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
- Local image cache: `/mnt/cache2/danbooru-ml-classifier/images` (configurable via `IMAGE_CACHE_DIR`)
- Local feature store: `/mnt/cache2/danbooru-ml-classifier/features` (configurable via `FEATURE_STORE_DIR`, see `worker/feature_store.py`)
- GPU scheduling: this machine runs one GPU-exclusive app at a time, managed by the sibling project `../HakataMatrix-app-controller-claude` (a resident Slackbot with llama-server, plus scheduled cronjob apps including this repo's `worker/main.py`). Any manual GPU work here (`worker/backfill_features.py`, `pu-learning/scripts/extract_features.py`, training, etc.) needs the resident app stopped first — confirm with the user before doing so, and check free VRAM with `rocm-smi --showmeminfo vram --json`.
- Required secrets (publisher cron): `PIXIV_SESSION_ID`, `DANBOORU_API_USER`, `DANBOORU_API_KEY`, `GELBOORU_API_USER`, `GELBOORU_API_KEY`
