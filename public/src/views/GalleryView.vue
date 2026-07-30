<script setup lang="ts">
import type {User} from 'firebase/auth';
import {ExternalLink, Info, Lock, Maximize, RefreshCw} from 'lucide-vue-next';
import {computed, nextTick, onMounted, onUnmounted, ref, watch} from 'vue';
import {useRouter} from 'vue-router';
import {
	type FavoritePoolItem,
	fetchFavoritesPool,
	fetchPostSource,
	getImageUrl,
} from '../api/mlApi';
import FavoriteButton from '../components/FavoriteButton.vue';
import {useFavorites} from '../composables/useFavorites';

const props = defineProps<{user: User | null}>();

const router = useRouter();
const {getCategories, toggleFavorite, hydrateFromImages} = useFavorites();

const PREFETCH_AHEAD = 3;
const PREFETCH_WINDOW = 8;
const THUMBS_BEFORE = 2;
const THUMBS_AFTER = 3;

// ── Pool + shuffled order ─────────────────────────────────────────────────────

const pool = ref<FavoritePoolItem[]>([]);
const order = ref<number[]>([]);
const cursor = ref(0);
const loading = ref(true);
const error = ref<string | null>(null);

function shuffle(n: number): number[] {
	const arr = Array.from({length: n}, (_, i) => i);
	for (let i = arr.length - 1; i > 0; i--) {
		const j = Math.floor(Math.random() * (i + 1));
		[arr[i], arr[j]] = [arr[j], arr[i]];
	}
	return arr;
}

const currentItem = computed<FavoritePoolItem | null>(() => {
	if (pool.value.length === 0) return null;
	const idx = order.value[cursor.value];
	return pool.value[idx] ?? null;
});

async function loadPool() {
	loading.value = true;
	error.value = null;
	try {
		const result = await fetchFavoritesPool();
		pool.value = result.images;
		hydrateFromImages(result.images);
		// Every reload (and the manual shuffle button) gets a fresh random
		// order -- no persistence across page loads is intentional here.
		order.value = shuffle(pool.value.length);
		cursor.value = 0;
	} catch (e) {
		error.value = (e as Error).message;
	} finally {
		loading.value = false;
	}
}

function reshuffle() {
	order.value = shuffle(pool.value.length);
	cursor.value = 0;
}

function reshuffleAvoidingRepeat() {
	const lastIndex = order.value[order.value.length - 1];
	const next = shuffle(pool.value.length);
	if (next.length > 1 && next[0] === lastIndex) {
		const swapAt = 1 + Math.floor(Math.random() * (next.length - 1));
		[next[0], next[swapAt]] = [next[swapAt], next[0]];
	}
	order.value = next;
	cursor.value = 0;
}

function next() {
	if (order.value.length === 0) return;
	if (cursor.value >= order.value.length - 1) {
		reshuffleAvoidingRepeat();
	} else {
		cursor.value++;
	}
}

function prev() {
	if (cursor.value > 0) {
		cursor.value--;
	}
}

function moveBy(delta: number) {
	const steps = Math.abs(delta);
	for (let i = 0; i < steps; i++) {
		if (delta > 0) next();
		else prev();
	}
}

// ── Prefetch ──────────────────────────────────────────────────────────────────

const prefetched = new Map<string, HTMLImageElement>();

function itemAt(offset: number): FavoritePoolItem | null {
	if (order.value.length === 0) return null;
	const idx =
		(((cursor.value + offset) % order.value.length) + order.value.length) %
		order.value.length;
	const poolIdx = order.value[idx];
	return pool.value[poolIdx] ?? null;
}

function prefetch() {
	for (let i = 1; i <= PREFETCH_AHEAD; i++) {
		const item = itemAt(i);
		if (!item || prefetched.has(item.id)) continue;
		const img = new Image();
		img.decoding = 'async';
		img.src = getImageUrl(item, false);
		prefetched.set(item.id, img);
	}
	// Prune the prefetch cache to a sliding window so long sessions don't
	// retain hundreds of decoded bitmaps.
	if (prefetched.size > PREFETCH_WINDOW) {
		const keep = new Set<string>();
		for (let i = 0; i <= PREFETCH_AHEAD; i++) {
			const item = itemAt(i);
			if (item) keep.add(item.id);
		}
		for (const id of prefetched.keys()) {
			if (!keep.has(id)) prefetched.delete(id);
		}
	}
}

watch(cursor, prefetch);

// Thumbnails shown in the right-edge strip: current + prev + next.
const thumbItems = computed(() => {
	const items: {item: FavoritePoolItem; offset: number}[] = [];
	for (let o = -THUMBS_BEFORE; o <= THUMBS_AFTER; o++) {
		const item = itemAt(o);
		if (item) items.push({item, offset: o});
	}
	return items;
});

// ── Rendering: keep the previous image visible until the new one loads ──────

const displayedItem = ref<FavoritePoolItem | null>(null);
const imageLoaded = ref(false);
const imageBroken = ref(false);
let brokenAdvanceTimer: number | null = null;

watch(currentItem, (item) => {
	imageLoaded.value = false;
	imageBroken.value = false;
	resetZoom();
	if (brokenAdvanceTimer !== null) {
		window.clearTimeout(brokenAdvanceTimer);
		brokenAdvanceTimer = null;
	}
	if (item) {
		// Swap immediately; the <img> keeps rendering the old src until its
		// own @load fires for the new src, avoiding a black flash.
		displayedItem.value = item;
	}
});

const naturalSize = ref<{w: number; h: number} | null>(null);

function onImageLoad(event: Event) {
	imageLoaded.value = true;
	const img = event.target as HTMLImageElement;
	naturalSize.value = {w: img.naturalWidth, h: img.naturalHeight};
}

function onImageError() {
	imageBroken.value = true;
	console.warn('Gallery: broken image', currentItem.value?.id);
	brokenAdvanceTimer = window.setTimeout(() => next(), 400);
}

// ── Zoom / pan ────────────────────────────────────────────────────────────────
// scale === 'fit' -> CSS object-contain, no scrolling.
// scale === number -> explicit pixel size (naturalSize * scale) inside a
// scrollable viewport; 1 means pixel-for-pixel (1 image px = 1 screen px).
//
// The zoomed image sits inside a "content" box sized to at least the
// viewport (Math.max(imageSize, viewportSize)), with the image centered
// within that box via an explicit offset. Without this, a zoomed image
// narrower/shorter than the viewport would be laid out flush at the
// scrollable area's top-left corner (scrollLeft/Top can't go negative to
// compensate), which reads as the image "jumping" to a corner on zoom.

const scale = ref<number | 'fit'>('fit');
const viewportEl = ref<HTMLElement | null>(null);
let isDragging = false;
let didDrag = false;
let dragStartX = 0;
let dragStartY = 0;
let dragScrollLeft = 0;
let dragScrollTop = 0;

function resetZoom() {
	scale.value = 'fit';
}

function clamp(value: number, min: number, max: number): number {
	return Math.min(Math.max(value, min), max);
}

function getFitRect(containerRect: DOMRect, w: number, h: number) {
	const containerRatio = containerRect.width / containerRect.height;
	const imageRatio = w / h;
	let renderW: number;
	let renderH: number;
	if (imageRatio > containerRatio) {
		renderW = containerRect.width;
		renderH = containerRect.width / imageRatio;
	} else {
		renderH = containerRect.height;
		renderW = containerRect.height * imageRatio;
	}
	return {
		renderW,
		renderH,
		offsetX: (containerRect.width - renderW) / 2,
		offsetY: (containerRect.height - renderH) / 2,
	};
}

/** Layout of the zoomed image: its pixel size plus its centering offset
 * within the (possibly larger) scrollable content box. */
const zoomedLayout = computed(() => {
	if (scale.value === 'fit' || !naturalSize.value) return null;
	const imageW = naturalSize.value.w * scale.value;
	const imageH = naturalSize.value.h * scale.value;
	const containerW = viewportEl.value?.clientWidth ?? imageW;
	const containerH = viewportEl.value?.clientHeight ?? imageH;
	const contentW = Math.max(imageW, containerW);
	const contentH = Math.max(imageH, containerH);
	return {
		imageW,
		imageH,
		contentW,
		contentH,
		offsetX: (contentW - imageW) / 2,
		offsetY: (contentH - imageH) / 2,
	};
});

function currentEffectiveScale(containerRect: DOMRect): number {
	if (scale.value !== 'fit') return scale.value;
	if (!naturalSize.value) return 1;
	const {renderW} = getFitRect(
		containerRect,
		naturalSize.value.w,
		naturalSize.value.h,
	);
	return renderW / naturalSize.value.w;
}

/** Natural-image-pixel coordinate under a given viewport-relative client point. */
function naturalPointFromClient(
	clientX: number,
	clientY: number,
): {nx: number; ny: number} | null {
	if (!viewportEl.value || !naturalSize.value) return null;
	const rect = viewportEl.value.getBoundingClientRect();
	const {w, h} = naturalSize.value;
	if (scale.value === 'fit') {
		const {renderW, renderH, offsetX, offsetY} = getFitRect(rect, w, h);
		const localX = clientX - rect.left - offsetX;
		const localY = clientY - rect.top - offsetY;
		return {
			nx: clamp(localX / renderW, 0, 1) * w,
			ny: clamp(localY / renderH, 0, 1) * h,
		};
	}
	const layout = zoomedLayout.value;
	if (!layout) return null;
	const localX =
		clientX - rect.left + viewportEl.value.scrollLeft - layout.offsetX;
	const localY =
		clientY - rect.top + viewportEl.value.scrollTop - layout.offsetY;
	return {nx: localX / scale.value, ny: localY / scale.value};
}

function setScaleCenteredOn(
	newScale: number,
	clientX: number,
	clientY: number,
) {
	const point = naturalPointFromClient(clientX, clientY);
	if (!point) return;
	scale.value = clamp(newScale, 0.1, 8);
	nextTick(() => {
		if (!viewportEl.value) return;
		const layout = zoomedLayout.value;
		if (!layout) return;
		const rect = viewportEl.value.getBoundingClientRect();
		const newScaleValue = scale.value as number;
		viewportEl.value.scrollLeft =
			point.nx * newScaleValue + layout.offsetX - (clientX - rect.left);
		viewportEl.value.scrollTop =
			point.ny * newScaleValue + layout.offsetY - (clientY - rect.top);
	});
}

function onImageAreaClick(event: MouseEvent) {
	if (didDrag) {
		didDrag = false;
		return;
	}
	if (scale.value === 'fit') {
		// Snap to exactly 100% (1 image pixel = 1 screen pixel), centered on
		// the clicked point.
		setScaleCenteredOn(1, event.clientX, event.clientY);
	} else {
		resetZoom();
	}
}

function onWheel(event: WheelEvent) {
	if (!viewportEl.value || !naturalSize.value) return;
	event.preventDefault();
	const rect = viewportEl.value.getBoundingClientRect();
	const current = currentEffectiveScale(rect);
	const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
	setScaleCenteredOn(current * factor, event.clientX, event.clientY);
}

function onMouseDown(event: MouseEvent) {
	if (scale.value === 'fit' || !viewportEl.value) return;
	event.preventDefault(); // suppress native image drag-ghost
	isDragging = true;
	didDrag = false;
	dragStartX = event.clientX;
	dragStartY = event.clientY;
	dragScrollLeft = viewportEl.value.scrollLeft;
	dragScrollTop = viewportEl.value.scrollTop;
}

function onMouseMove(event: MouseEvent) {
	if (!isDragging || !viewportEl.value) return;
	const dx = event.clientX - dragStartX;
	const dy = event.clientY - dragStartY;
	if (Math.abs(dx) > 3 || Math.abs(dy) > 3) didDrag = true;
	viewportEl.value.scrollLeft = dragScrollLeft - dx;
	viewportEl.value.scrollTop = dragScrollTop - dy;
}

function onMouseUp() {
	isDragging = false;
}

// ── Source link ───────────────────────────────────────────────────────────────
// Derived entirely from `key` (provider/filename), matching the logic in
// DailyRecommendationView.vue -- the lean /favorites/pool projection doesn't
// carry Twitter's `source.tweetId`, so Twitter items have no derivable
// source link here (same as elsewhere in the app when that field is absent).

function getProvider(item: FavoritePoolItem): string {
	return item.key?.split('/')[0] ?? item.type;
}

function getStem(item: FavoritePoolItem): string {
	const parts = item.key?.split('/') ?? [];
	if (parts.length < 2) return '';
	return parts[parts.length - 1].replace(/\.[^.]+$/, '');
}

function getPostPageUrl(
	provider: 'danbooru' | 'gelbooru',
	stem: string,
): string {
	if (provider === 'danbooru')
		return `https://danbooru.donmai.us/posts/${stem}`;
	return `https://gelbooru.com/index.php?page=post&s=view&id=${stem}`;
}

const directSourceUrl = computed((): string | null => {
	if (!displayedItem.value) return null;
	const provider = getProvider(displayedItem.value);
	const stem = getStem(displayedItem.value);
	if (provider === 'pixiv') {
		const id = stem.replace(/(-[0-9a-f]{32})?_p\d+$/, '');
		return `https://www.pixiv.net/artworks/${id}`;
	}
	if (provider === 'sankaku')
		return `https://chan.sankakucomplex.com/ja/posts/${stem}`;
	return null;
});

const canViewSource = computed(() => {
	if (!displayedItem.value) return false;
	const provider = getProvider(displayedItem.value);
	return ['pixiv', 'sankaku', 'danbooru', 'gelbooru'].includes(provider);
});

const sourceLoading = ref(false);

async function openSource() {
	if (!displayedItem.value) return;
	const provider = getProvider(displayedItem.value);

	if (provider === 'danbooru' || provider === 'gelbooru') {
		const stem = getStem(displayedItem.value);
		const fallback = getPostPageUrl(provider, stem);
		sourceLoading.value = true;
		try {
			const source = await fetchPostSource(provider, stem);
			const url =
				source &&
				(source.startsWith('http://') || source.startsWith('https://'))
					? source
					: fallback;
			window.open(url, '_blank', 'noopener,noreferrer');
		} catch {
			window.open(fallback, '_blank', 'noopener,noreferrer');
		} finally {
			sourceLoading.value = false;
		}
		return;
	}

	if (directSourceUrl.value) {
		window.open(directSourceUrl.value, '_blank', 'noopener,noreferrer');
	}
}

// ── Keyboard ────────────────────────────────────────────────────────────────

function isTypingTarget(target: EventTarget | null): boolean {
	if (!(target instanceof HTMLElement)) return false;
	return (
		target.tagName === 'INPUT' ||
		target.tagName === 'TEXTAREA' ||
		target.isContentEditable
	);
}

function openDetails() {
	if (!currentItem.value) return;
	const route = router.resolve({
		name: 'daily-image-detail',
		params: {id: currentItem.value.id},
	});
	window.open(route.href, '_blank', 'noopener,noreferrer');
}

async function toggleFavoriteCurrent() {
	if (!currentItem.value) return;
	try {
		await toggleFavorite(currentItem.value.id);
	} catch (e) {
		console.error('Failed to toggle favorite:', e);
	}
}

function handleKeydown(event: KeyboardEvent) {
	if (isTypingTarget(event.target)) return;
	switch (event.key) {
		case 'ArrowRight':
		case ' ':
		case 'Enter':
			event.preventDefault();
			next();
			break;
		case 'ArrowLeft':
			event.preventDefault();
			prev();
			break;
		case 'f':
			toggleFavoriteCurrent();
			break;
		case 'd':
			openDetails();
			break;
	}
}

// ── Touch swipe (navigation only; zoom/pan is mouse-driven) ─────────────────

let touchStartX = 0;
let touchStartY = 0;

function onTouchStart(event: TouchEvent) {
	const t = event.touches[0];
	touchStartX = t.clientX;
	touchStartY = t.clientY;
}

function onTouchEnd(event: TouchEvent) {
	if (scale.value !== 'fit') return;
	const t = event.changedTouches[0];
	const dx = t.clientX - touchStartX;
	const dy = t.clientY - touchStartY;
	if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
		if (dx < 0) next();
		else prev();
	}
}

// ── Fullscreen ──────────────────────────────────────────────────────────────

const containerEl = ref<HTMLElement | null>(null);
const isFullscreen = ref(false);

function toggleFullscreen() {
	if (document.fullscreenElement) {
		document.exitFullscreen().catch(() => {});
	} else {
		containerEl.value?.requestFullscreen().catch(() => {});
	}
}

function onFullscreenChange() {
	isFullscreen.value = document.fullscreenElement !== null;
}

onMounted(() => {
	window.addEventListener('keydown', handleKeydown);
	window.addEventListener('mousemove', onMouseMove);
	window.addEventListener('mouseup', onMouseUp);
	document.addEventListener('fullscreenchange', onFullscreenChange);
	if (props.user) loadPool();
});

onUnmounted(() => {
	window.removeEventListener('keydown', handleKeydown);
	window.removeEventListener('mousemove', onMouseMove);
	window.removeEventListener('mouseup', onMouseUp);
	document.removeEventListener('fullscreenchange', onFullscreenChange);
	if (brokenAdvanceTimer !== null) window.clearTimeout(brokenAdvanceTimer);
});

watch(
	() => props.user,
	(u) => {
		if (u && pool.value.length === 0) loadPool();
	},
);
</script>

<template>
	<div>
		<!-- Auth Required -->
		<div
			v-if="!user"
			class="bg-white rounded-2xl shadow-lg p-12 text-center max-w-md mx-auto"
		>
			<div
				class="w-20 h-20 bg-blue-100 rounded-full flex items-center justify-center mx-auto mb-6"
			>
				<Lock :size="40" class="text-blue-500" />
			</div>
			<h2 class="text-2xl font-bold text-gray-900 mb-3">
				Authentication Required
			</h2>
			<p class="text-gray-600">
				Please login with your Google account to view the Gallery.
			</p>
		</div>

		<template v-else>
			<div v-if="loading" class="flex justify-center items-center py-20">
				<div
					class="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent"
				/>
			</div>

			<div
				v-else-if="error"
				class="bg-red-50 border border-red-200 rounded-xl p-6 text-center"
			>
				<p class="text-red-700">Error: {{ error }}</p>
			</div>

			<div
				v-else-if="pool.length === 0"
				class="bg-white rounded-xl shadow-md p-12 text-center"
			>
				<p class="text-gray-600">
					No favorites yet. Add some from Daily Recommendation or Favorites
					first.
				</p>
			</div>

			<div
				v-else
				ref="containerEl"
				class="relative bg-black rounded-xl overflow-hidden -mx-4 sm:mx-0"
				style="height: calc(100vh - 8rem)"
				@touchstart.passive="onTouchStart"
				@touchend="onTouchEnd"
			>
				<!-- Loading spinner while the current image hasn't loaded yet -->
				<div
					v-if="!imageLoaded && !imageBroken"
					class="absolute inset-0 flex items-center justify-center z-10 pointer-events-none"
				>
					<div
						class="animate-spin rounded-full h-10 w-10 border-4 border-white/40 border-t-white"
					/>
				</div>

				<div
					v-if="imageBroken"
					class="absolute inset-0 flex items-center justify-center z-10 text-white/70 text-sm"
				>
					Image unavailable, skipping…
				</div>

				<!-- Fit mode: simple flex-centered, no scrolling -->
				<div
					v-if="scale === 'fit'"
					ref="viewportEl"
					class="w-full h-full flex items-center justify-center"
					@wheel.prevent="onWheel"
				>
					<img
						v-if="displayedItem"
						:key="displayedItem.id"
						:src="getImageUrl(displayedItem, false)"
						:alt="displayedItem.id"
						draggable="false"
						class="max-w-full max-h-full object-contain select-none cursor-zoom-in"
						@load="onImageLoad"
						@error="onImageError"
						@click="onImageAreaClick"
						@dragstart.prevent
					>
				</div>
				<!-- Zoomed mode: explicit pixel size, centered within a scrollable/pannable content box -->
				<div
					v-else
					ref="viewportEl"
					class="w-full h-full overflow-auto"
					:class="isDragging ? 'cursor-grabbing' : 'cursor-zoom-out'"
					@wheel.prevent="onWheel"
					@mousedown="onMouseDown"
				>
					<div
						v-if="zoomedLayout"
						class="relative"
						:style="{width: `${zoomedLayout.contentW}px`, height: `${zoomedLayout.contentH}px`}"
					>
						<img
							v-if="displayedItem"
							:key="displayedItem.id"
							:src="getImageUrl(displayedItem, false)"
							:alt="displayedItem.id"
							draggable="false"
							class="absolute select-none"
							:style="{
								left: `${zoomedLayout.offsetX}px`,
								top: `${zoomedLayout.offsetY}px`,
								width: `${zoomedLayout.imageW}px`,
								height: `${zoomedLayout.imageH}px`,
							}"
							@load="onImageLoad"
							@error="onImageError"
							@click="onImageAreaClick"
							@dragstart.prevent
						>
					</div>
				</div>

				<!-- Left/right click zones for prev/next, overlaid on the image area
				     (disabled while zoomed, so edge-dragging pans instead of
				     navigating). Clamped to at most 30% of the width each so the
				     center click-to-zoom area always survives. -->
				<button
					v-if="scale === 'fit'"
					type="button"
					class="absolute inset-y-0 left-0 w-[min(384px,30%)] z-10"
					aria-label="Previous"
					@click="prev"
				/>
				<button
					v-if="scale === 'fit'"
					type="button"
					class="absolute inset-y-0 right-0 w-[min(384px,30%)] z-10 sm:right-36"
					aria-label="Next"
					@click="next"
				/>

				<!-- Right-edge thumbnail strip (prev, current, next) -->
				<div
					v-if="thumbItems.length > 0"
					class="hidden sm:flex absolute inset-y-0 right-0 w-36 flex-col items-center justify-center gap-2 py-4 z-20 bg-gradient-to-l from-black/40 to-transparent"
				>
					<button
						v-for="{ item, offset } in thumbItems"
						:key="item.id"
						type="button"
						class="w-28 h-28 flex-shrink-0 rounded-md overflow-hidden border-2 transition-all hover:scale-105"
						:class="
							offset === 0
								? 'border-blue-400 ring-2 ring-blue-400/50'
								: offset < 0
									? 'border-white/20 opacity-60'
									: 'border-white/40 opacity-90'
						"
						:title="offset === 0 ? 'Current' : offset < 0 ? `${-offset} back` : `${offset} ahead`"
						@click="moveBy(offset)"
					>
						<img
							:src="getImageUrl(item, true)"
							:alt="item.id"
							class="w-full h-full object-cover"
							loading="lazy"
						>
					</button>
				</div>

				<!-- Chrome -->
				<div
					class="absolute top-3 left-3 right-3 flex items-center justify-between z-30"
				>
					<span
						class="px-2 py-1 bg-black/50 text-white text-xs rounded-md font-mono"
					>
						{{ cursor + 1 }}
						/ {{ order.length }}
					</span>
					<div class="flex items-center gap-2">
						<span
							v-if="displayedItem && getCategories(displayedItem.id).length > 0"
							class="px-2 py-1 bg-black/50 text-white text-xs rounded-md"
						>
							{{ getCategories(displayedItem.id).join(', ') }}
						</span>
						<FavoriteButton
							v-if="displayedItem"
							:image-id="displayedItem.id"
							:size="18"
							variant="overlay"
						/>
						<button
							type="button"
							class="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors"
							title="Shuffle"
							@click="reshuffle"
						>
							<RefreshCw :size="18" />
						</button>
						<button
							v-if="canViewSource"
							type="button"
							:disabled="sourceLoading"
							class="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors disabled:opacity-50 disabled:cursor-wait"
							title="View Source"
							@click="openSource"
						>
							<ExternalLink :size="18" />
						</button>
						<button
							type="button"
							class="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors"
							title="Details"
							@click="openDetails"
						>
							<Info :size="18" />
						</button>
						<button
							type="button"
							class="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors"
							:title="isFullscreen ? 'Exit fullscreen' : 'Fullscreen'"
							@click="toggleFullscreen"
						>
							<Maximize :size="18" />
						</button>
					</div>
				</div>

				<!-- Desktop prev/next arrow icons (decorative -- the click zones above handle the actual clicks) -->
				<button
					v-if="scale === 'fit'"
					type="button"
					class="hidden sm:flex absolute left-4 top-1/2 -translate-y-1/2 p-3 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors z-20 pointer-events-none"
				>
					<svg
						aria-hidden="true"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						class="w-6 h-6"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M15 19l-7-7 7-7"
						/>
					</svg>
				</button>
				<button
					v-if="scale === 'fit'"
					type="button"
					class="hidden sm:flex absolute right-40 top-1/2 -translate-y-1/2 p-3 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors z-20 pointer-events-none"
				>
					<svg
						aria-hidden="true"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						class="w-6 h-6"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M9 5l7 7-7 7"
						/>
					</svg>
				</button>
			</div>
			<p class="text-center text-xs text-gray-400 mt-2">
				←/→ or Space to navigate · F to favorite · D for details · click image
				to zoom to 100%, drag to pan
			</p>
		</template>
	</div>
</template>
