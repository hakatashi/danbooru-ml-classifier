<script setup lang="ts">
import type {User} from 'firebase/auth';
import {Info, Lock, Maximize, X} from 'lucide-vue-next';
import {computed, onMounted, onUnmounted, ref, watch} from 'vue';
import {useRouter} from 'vue-router';
import {
	type FavoritePoolItem,
	fetchFavoritesPool,
	getImageUrl,
} from '../api/mlApi';
import FavoriteButton from '../components/FavoriteButton.vue';
import {useFavorites} from '../composables/useFavorites';

const props = defineProps<{user: User | null}>();

const router = useRouter();
const {getCategories, toggleFavorite} = useFavorites();

const SESSION_KEY = 'dmc-gallery-session-v1';
const PREFETCH_AHEAD = 3;
const PREFETCH_WINDOW = 8;

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

interface StoredSession {
	poolLength: number;
	order: number[];
	cursor: number;
}

function loadStoredSession(poolLength: number): StoredSession | null {
	try {
		const raw = sessionStorage.getItem(SESSION_KEY);
		if (!raw) return null;
		const parsed = JSON.parse(raw) as StoredSession;
		if (parsed.poolLength !== poolLength) return null;
		if (!Array.isArray(parsed.order) || parsed.order.length !== poolLength)
			return null;
		return parsed;
	} catch {
		return null;
	}
}

function persistSession() {
	const session: StoredSession = {
		poolLength: pool.value.length,
		order: order.value,
		cursor: cursor.value,
	};
	try {
		sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
	} catch {
		// ignore quota errors
	}
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
		if (pool.value.length === 0) {
			return;
		}
		const stored = loadStoredSession(pool.value.length);
		if (stored) {
			order.value = stored.order;
			cursor.value = Math.min(stored.cursor, order.value.length - 1);
		} else {
			order.value = shuffle(pool.value.length);
			cursor.value = 0;
		}
		persistSession();
	} catch (e) {
		error.value = (e as Error).message;
	} finally {
		loading.value = false;
	}
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
	persistSession();
}

function prev() {
	if (cursor.value > 0) {
		cursor.value--;
		persistSession();
	}
}

// ── Prefetch ──────────────────────────────────────────────────────────────────

const prefetched = new Map<string, HTMLImageElement>();

function itemAt(offset: number): FavoritePoolItem | null {
	if (order.value.length === 0) return null;
	const idx = (cursor.value + offset) % order.value.length;
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

// ── Rendering: keep the previous image visible until the new one loads ──────

const displayedItem = ref<FavoritePoolItem | null>(null);
const imageLoaded = ref(false);
const imageBroken = ref(false);
let brokenAdvanceTimer: number | null = null;

watch(currentItem, (item) => {
	imageLoaded.value = false;
	imageBroken.value = false;
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

function onImageLoad() {
	imageLoaded.value = true;
}

function onImageError() {
	imageBroken.value = true;
	console.warn('Gallery: broken image', currentItem.value?.id);
	brokenAdvanceTimer = window.setTimeout(() => next(), 400);
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
		case 'Escape':
			router.back();
			break;
	}
}

// ── Touch swipe ───────────────────────────────────────────────────────────────

let touchStartX = 0;
let touchStartY = 0;

function onTouchStart(event: TouchEvent) {
	const t = event.touches[0];
	touchStartX = t.clientX;
	touchStartY = t.clientY;
}

function onTouchEnd(event: TouchEvent) {
	const t = event.changedTouches[0];
	const dx = t.clientX - touchStartX;
	const dy = t.clientY - touchStartY;
	if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
		if (dx < 0) next();
		else prev();
		return;
	}
	// Tap on the left/right third of the screen for one-handed navigation.
	if (Math.abs(dx) < 10 && Math.abs(dy) < 10) {
		const width = window.innerWidth;
		if (t.clientX < width / 3) prev();
		else if (t.clientX > (width * 2) / 3) next();
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
	document.addEventListener('fullscreenchange', onFullscreenChange);
	if (props.user) loadPool();
});

onUnmounted(() => {
	window.removeEventListener('keydown', handleKeydown);
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

				<img
					v-if="displayedItem"
					:key="displayedItem.id"
					:src="getImageUrl(displayedItem, false)"
					:alt="displayedItem.id"
					class="w-full h-full object-contain select-none"
					@load="onImageLoad"
					@error="onImageError"
				>

				<!-- Left/right tap zones (invisible, for one-handed nav) -->
				<button
					type="button"
					class="absolute inset-y-0 left-0 w-16 sm:hidden"
					aria-label="Previous"
					@click="prev"
				/>
				<button
					type="button"
					class="absolute inset-y-0 right-0 w-16 sm:hidden"
					aria-label="Next"
					@click="next"
				/>

				<!-- Chrome -->
				<div
					class="absolute top-3 left-3 right-3 flex items-center justify-between z-20"
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
						<button
							type="button"
							class="p-2 rounded-lg bg-white/10 hover:bg-white/20 text-white transition-colors"
							title="Exit"
							@click="router.back()"
						>
							<X :size="18" />
						</button>
					</div>
				</div>

				<!-- Desktop prev/next arrows -->
				<button
					type="button"
					class="hidden sm:flex absolute left-4 top-1/2 -translate-y-1/2 p-3 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors z-20"
					title="Previous"
					@click="prev"
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
					type="button"
					class="hidden sm:flex absolute right-4 top-1/2 -translate-y-1/2 p-3 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors z-20"
					title="Next"
					@click="next"
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
				←/→ or Space to navigate · F to favorite · D for details · Esc to exit
			</p>
		</template>
	</div>
</template>
