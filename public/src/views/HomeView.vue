<script setup lang="ts">
import type {User} from 'firebase/auth';
import {Heart, Image as ImageIcon, Lock, TrendingUp} from 'lucide-vue-next';
import {computed, onMounted, onUnmounted, ref, watch} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import FilterBar from '../components/FilterBar.vue';
import ImageCard from '../components/ImageCard.vue';
import ImageLightbox from '../components/ImageLightbox.vue';
import {
	type AgeFilter,
	type RatingFilter,
	type SortOption,
	useImages,
} from '../composables/useImages';
import type {ImageDocument} from '../types';

const props = defineProps<{
	user: User | null;
}>();

const router = useRouter();
const route = useRoute();

const {
	loading,
	error,
	hasNextPage,
	hasPrevPage,
	loadPage,
	toggleFavorite,
	isFavorite,
} = useImages();
const currentPage = ref(0);
const currentSort = ref<SortOption | null>(null);
const currentImages = ref<ImageDocument[]>([]);
const galleryMode = ref(false);
const lightboxImage = ref<string | null>(null);
const lightboxAlt = ref<string>('');
const imageAspectRatios = ref<Map<string, number>>(new Map());
const galleryRows = ref<Array<Array<ImageDocument & {height: number}>>>([]);
const savingFavorites = ref<Set<string>>(new Set());

// Sort options mapping
const sortOptionsMap = {
	'joycaption-desc': {
		field: 'moderations.joycaption.result',
		direction: 'desc' as const,
	},
	'joycaption-asc': {
		field: 'moderations.joycaption.result',
		direction: 'asc' as const,
	},
	'minicpm-desc': {
		field: 'moderations.minicpm.result',
		direction: 'desc' as const,
	},
	'minicpm-asc': {
		field: 'moderations.minicpm.result',
		direction: 'asc' as const,
	},
	'joycaption-age-desc': {
		field: 'ageEstimations.joycaption.main_character_age',
		direction: 'desc' as const,
	},
	'joycaption-age-asc': {
		field: 'ageEstimations.joycaption.main_character_age',
		direction: 'asc' as const,
	},
	'minicpm-age-desc': {
		field: 'ageEstimations.minicpm.main_character_age',
		direction: 'desc' as const,
	},
	'minicpm-age-asc': {
		field: 'ageEstimations.minicpm.main_character_age',
		direction: 'asc' as const,
	},
	'qwen3-age-desc': {
		field: 'ageEstimations.qwen3.main_character_age',
		direction: 'desc' as const,
	},
	'qwen3-age-asc': {
		field: 'ageEstimations.qwen3.main_character_age',
		direction: 'asc' as const,
	},
	'joycaption-created-desc': {
		field: 'captions.joycaption.metadata.createdAt',
		direction: 'desc' as const,
	},
	'joycaption-created-asc': {
		field: 'captions.joycaption.metadata.createdAt',
		direction: 'asc' as const,
	},
	'minicpm-created-desc': {
		field: 'captions.minicpm.metadata.createdAt',
		direction: 'desc' as const,
	},
	'minicpm-created-asc': {
		field: 'captions.minicpm.metadata.createdAt',
		direction: 'asc' as const,
	},
};

// Get sort and page values from query params
const sortValue = computed(() => {
	const sortQuery = route.query.sort;
	return typeof sortQuery === 'string' ? sortQuery : 'minicpm-created-desc';
});

const pageValue = computed(() => {
	const pageQuery = route.query.page;
	if (typeof pageQuery === 'string') {
		const parsed = Number.parseInt(pageQuery, 10);
		return Number.isNaN(parsed) || parsed < 0 ? 0 : parsed;
	}
	return 0;
});

const ratingProviderValue = computed(() => {
	const providerQuery = route.query.ratingProvider;
	if (
		typeof providerQuery === 'string' &&
		(providerQuery === 'joycaption' || providerQuery === 'minicpm')
	) {
		return providerQuery;
	}
	return 'minicpm';
});

const ratingMinValue = computed(() => {
	const minQuery = route.query.ratingMin;
	if (typeof minQuery === 'string') {
		const parsed = Number.parseInt(minQuery, 10);
		return Number.isNaN(parsed) ? null : parsed;
	}
	return null;
});

const ratingMaxValue = computed(() => {
	const maxQuery = route.query.ratingMax;
	if (typeof maxQuery === 'string') {
		const parsed = Number.parseInt(maxQuery, 10);
		return Number.isNaN(parsed) ? null : parsed;
	}
	return null;
});

const ageProviderValue = computed(() => {
	const providerQuery = route.query.ageProvider;
	if (
		typeof providerQuery === 'string' &&
		(providerQuery === 'joycaption' || providerQuery === 'minicpm')
	) {
		return providerQuery;
	}
	return 'minicpm';
});

const ageMinValue = computed(() => {
	const minQuery = route.query.ageMin;
	if (typeof minQuery === 'string') {
		const parsed = Number.parseInt(minQuery, 10);
		return Number.isNaN(parsed) ? null : parsed;
	}
	return null;
});

const ageMaxValue = computed(() => {
	const maxQuery = route.query.ageMax;
	if (typeof maxQuery === 'string') {
		const parsed = Number.parseInt(maxQuery, 10);
		return Number.isNaN(parsed) ? null : parsed;
	}
	return null;
});

// Watch for user authentication and query parameter changes
watch(
	[
		() => props.user,
		sortValue,
		pageValue,
		ratingProviderValue,
		ratingMinValue,
		ratingMaxValue,
		ageProviderValue,
		ageMinValue,
		ageMaxValue,
	],
	async ([
		newUser,
		newSort,
		newPage,
		newRatingProvider,
		newRatingMin,
		newRatingMax,
		newAgeProvider,
		newAgeMin,
		newAgeMax,
	]) => {
		if (newUser && newSort) {
			const sortOption =
				sortOptionsMap[newSort as keyof typeof sortOptionsMap] ||
				sortOptionsMap['minicpm-created-desc'];
			currentSort.value = sortOption;

			const ratingFilter: RatingFilter = {
				provider: newRatingProvider,
				min: newRatingMin,
				max: newRatingMax,
			};

			const ageFilter: AgeFilter = {
				provider: newAgeProvider,
				min: newAgeMin,
				max: newAgeMax,
			};

			const result = await loadPage(
				sortOption,
				newPage,
				ratingFilter,
				ageFilter,
			);
			currentImages.value = result.images;
			currentPage.value = result.page;

			// Reset gallery state when images change
			imageAspectRatios.value.clear();
			galleryRows.value = [];
		}
	},
	{immediate: true},
);

const canGoNext = computed(() => {
	return hasNextPage.value && currentImages.value.length > 0;
});

const canGoPrev = computed(() => {
	return hasPrevPage.value;
});

async function onSortChange(_sort: SortOption, sortKey: string) {
	// Update URL with new sort, reset page to 0
	await router.push({
		query: {
			sort: sortKey,
			page: '0',
		},
	});
}

async function onPageChange(page: number) {
	if (!currentSort.value || !props.user) return;

	window.scrollTo({top: 0, behavior: 'smooth'});

	// Update URL with new page, preserve all filters
	const query: Record<string, string> = {
		sort: sortValue.value,
		page: page.toString(),
	};

	if (ratingProviderValue.value !== 'minicpm') {
		query.ratingProvider = ratingProviderValue.value;
	}
	if (ratingMinValue.value !== null) {
		query.ratingMin = ratingMinValue.value.toString();
	}
	if (ratingMaxValue.value !== null) {
		query.ratingMax = ratingMaxValue.value.toString();
	}
	if (ageProviderValue.value !== 'minicpm') {
		query.ageProvider = ageProviderValue.value;
	}
	if (ageMinValue.value !== null) {
		query.ageMin = ageMinValue.value.toString();
	}
	if (ageMaxValue.value !== null) {
		query.ageMax = ageMaxValue.value.toString();
	}

	await router.push({query});
}

async function onRatingChange(ratingFilter: RatingFilter) {
	// Update URL with new rating filter, reset page to 0
	const query: Record<string, string> = {
		sort: sortValue.value,
		page: '0',
	};

	if (ratingFilter.provider !== 'minicpm') {
		query.ratingProvider = ratingFilter.provider;
	}
	if (ratingFilter.min !== null) {
		query.ratingMin = ratingFilter.min.toString();
	}
	if (ratingFilter.max !== null) {
		query.ratingMax = ratingFilter.max.toString();
	}

	// Preserve age filters
	if (ageProviderValue.value !== 'minicpm') {
		query.ageProvider = ageProviderValue.value;
	}
	if (ageMinValue.value !== null) {
		query.ageMin = ageMinValue.value.toString();
	}
	if (ageMaxValue.value !== null) {
		query.ageMax = ageMaxValue.value.toString();
	}

	await router.push({query});
}

async function onAgeChange(ageFilter: AgeFilter) {
	// Update URL with new age filter, reset page to 0
	const query: Record<string, string> = {
		sort: sortValue.value,
		page: '0',
	};

	// Preserve rating filters
	if (ratingProviderValue.value !== 'minicpm') {
		query.ratingProvider = ratingProviderValue.value;
	}
	if (ratingMinValue.value !== null) {
		query.ratingMin = ratingMinValue.value.toString();
	}
	if (ratingMaxValue.value !== null) {
		query.ratingMax = ratingMaxValue.value.toString();
	}

	// Add age filters
	if (ageFilter.provider !== 'minicpm') {
		query.ageProvider = ageFilter.provider;
	}
	if (ageFilter.min !== null) {
		query.ageMin = ageFilter.min.toString();
	}
	if (ageFilter.max !== null) {
		query.ageMax = ageFilter.max.toString();
	}

	await router.push({query});
}

function onGalleryModeChange(enabled: boolean) {
	galleryMode.value = enabled;
}

function getImageUrl(image: ImageDocument): string {
	const IMAGE_BASE_URL =
		'https://matrix.hakatashi.com/images/hakataarchive/twitter/';
	const filename = image.key ? image.key.split('/').pop() : image.id;
	return IMAGE_BASE_URL + filename;
}

function getDecodedId(image: ImageDocument): string {
	return decodeURIComponent(image.id);
}

function getRatingColorClass(rating: number | null): string {
	if (rating === null) return 'bg-gray-500';
	if (rating <= 2) return 'bg-green-500';
	if (rating <= 4) return 'bg-lime-500';
	if (rating <= 6) return 'bg-orange-500';
	if (rating <= 8) return 'bg-red-500';
	return 'bg-purple-500';
}

function openLightbox(image: ImageDocument) {
	lightboxImage.value = getImageUrl(image);
	lightboxAlt.value = image.id;
}

function closeLightbox() {
	lightboxImage.value = null;
	lightboxAlt.value = '';
}

function handleImageLoad(event: Event, imageId: string) {
	const img = event.target as HTMLImageElement;
	const aspectRatio = img.naturalWidth / img.naturalHeight;
	imageAspectRatios.value.set(imageId, aspectRatio);

	// すべての画像がロードされたら行を再計算
	if (imageAspectRatios.value.size === currentImages.value.length) {
		calculateGalleryRows();
	}
}

function calculateGalleryRows() {
	const containerWidth = window.innerWidth - 32; // padding考慮
	const rowGap = 8; // gap-2 = 8px
	const targetRowHeight = 480;
	const images = currentImages.value;

	const rows: Array<Array<ImageDocument & {height: number}>> = [];
	let currentRow: Array<ImageDocument & {height: number}> = [];
	let currentRowWidth = 0;

	for (const image of images) {
		const aspectRatio = imageAspectRatios.value.get(image.id);
		if (!aspectRatio) continue;

		// 縦横比を制限
		let constrainedAspectRatio = aspectRatio;
		if (aspectRatio > 2) constrainedAspectRatio = 2;
		else if (aspectRatio < 0.5) constrainedAspectRatio = 0.5;

		const imageWidth = targetRowHeight * constrainedAspectRatio;

		// 現在の行に追加できるかチェック
		const wouldBeWidth =
			currentRowWidth + imageWidth + (currentRow.length > 0 ? rowGap : 0);

		if (currentRow.length > 0 && wouldBeWidth > containerWidth) {
			// 現在の行を確定し、高さを調整
			const totalWidth = currentRowWidth;
			const scale = containerWidth / totalWidth;
			const rowHeight = targetRowHeight * scale;

			for (const img of currentRow) {
				img.height = rowHeight;
			}

			rows.push(currentRow);
			currentRow = [];
			currentRowWidth = 0;
		}

		currentRow.push({...image, height: targetRowHeight});
		currentRowWidth += imageWidth + (currentRow.length > 1 ? rowGap : 0);
	}

	// 最後の行を追加（幅が足りなくてもそのまま）
	if (currentRow.length > 0) {
		rows.push(currentRow);
	}

	galleryRows.value = rows;
}

function getGalleryImageContainerStyle(imageId: string, height: number) {
	const aspectRatio = imageAspectRatios.value.get(imageId);
	if (!aspectRatio) return {};

	let width: number;

	// 横長すぎる (2:1より横長) → 2:1の比率で表示
	if (aspectRatio > 2) {
		width = height * 2;
	}
	// 縦長すぎる (1:2より縦長) → 1:2の比率で表示
	else if (aspectRatio < 0.5) {
		width = height / 2;
	}
	// 通常の縦横比 → そのまま表示
	else {
		width = height * aspectRatio;
	}

	return {width: `${width}px`, height: `${height}px`};
}

function getGalleryImageStyle(imageId: string, height: number) {
	const aspectRatio = imageAspectRatios.value.get(imageId);
	if (!aspectRatio) return {};

	let width: number;
	let objectFit: 'cover' | 'contain';

	// 横長すぎる (2:1より横長) → 2:1の比率で表示
	if (aspectRatio > 2) {
		width = height * 2;
		objectFit = 'cover';
	}
	// 縦長すぎる (1:2より縦長) → 1:2の比率で表示
	else if (aspectRatio < 0.5) {
		width = height / 2;
		objectFit = 'cover';
	}
	// 通常の縦横比 → そのまま表示
	else {
		width = height * aspectRatio;
		objectFit = 'contain';
	}

	return {
		width: `${width}px`,
		height: `${height}px`,
		objectFit,
	};
}

// Window resize handler for gallery mode
let resizeTimeout: number | null = null;

function handleResize() {
	// Debounce resize events
	if (resizeTimeout !== null) {
		window.clearTimeout(resizeTimeout);
	}

	resizeTimeout = window.setTimeout(() => {
		// Recalculate gallery rows if we have aspect ratios
		if (
			galleryMode.value &&
			imageAspectRatios.value.size === currentImages.value.length
		) {
			calculateGalleryRows();
		}
	}, 300);
}

// Add/remove resize listener
onMounted(() => {
	window.addEventListener('resize', handleResize);
});

onUnmounted(() => {
	window.removeEventListener('resize', handleResize);
	if (resizeTimeout !== null) {
		window.clearTimeout(resizeTimeout);
	}
});

async function handleToggleFavorite(event: Event, imageId: string) {
	event.preventDefault();
	event.stopPropagation();

	if (savingFavorites.value.has(imageId)) return;

	savingFavorites.value.add(imageId);
	try {
		await toggleFavorite(imageId);
	} catch (error) {
		console.error('Failed to toggle favorite:', error);
	} finally {
		savingFavorites.value.delete(imageId);
	}
}
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
				<Lock :size="40" class="text-blue-500"/>
			</div>
			<h2 class="text-2xl font-bold text-gray-900 mb-3">
				Authentication Required
			</h2>
			<p class="text-gray-600 mb-6">
				Please login with your Google account to view the VLM results.
			</p>
		</div>

		<!-- Main Content -->
		<template v-else>
			<FilterBar
				:current-page="currentPage"
				:current-sort="sortValue"
				:current-rating-provider="ratingProviderValue"
				:current-rating-min="ratingMinValue"
				:current-rating-max="ratingMaxValue"
				:current-age-provider="ageProviderValue"
				:current-age-min="ageMinValue"
				:current-age-max="ageMaxValue"
				:can-go-next="canGoNext"
				:can-go-prev="canGoPrev"
				:gallery-mode="galleryMode"
				@sort-change="onSortChange"
				@page-change="onPageChange"
				@rating-change="onRatingChange"
				@age-change="onAgeChange"
				@gallery-mode-change="onGalleryModeChange"
			/>

			<!-- Loading State -->
			<div
				v-if="loading && currentImages.length === 0"
				class="flex justify-center items-center py-20"
			>
				<div class="flex flex-col items-center gap-4">
					<div
						class="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent"
					></div>
					<p class="text-gray-600">Loading images...</p>
				</div>
			</div>

			<!-- Error State -->
			<div
				v-else-if="error"
				class="bg-red-50 border border-red-200 rounded-xl p-6 text-center"
			>
				<p class="text-red-700">Error: {{ error }}</p>
			</div>

			<!-- Empty State -->
			<div
				v-else-if="currentImages.length === 0"
				class="bg-white rounded-xl shadow-md p-12 text-center"
			>
				<div
					class="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4"
				>
					<ImageIcon :size="32" class="text-gray-400"/>
				</div>
				<p class="text-gray-600">No images found</p>
			</div>

			<!-- Gallery Mode -->
			<div v-else-if="galleryMode">
				<!-- Justified rows (after all images loaded) -->
				<div v-if="galleryRows.length > 0" class="flex flex-col gap-2">
					<div
						v-for="(row, rowIndex) in galleryRows"
						:key="rowIndex"
						class="flex gap-2 justify-center"
					>
						<div
							v-for="image in row"
							:key="image.id"
							class="relative flex-shrink-0 group cursor-pointer overflow-hidden"
							:style="getGalleryImageContainerStyle(image.id, image.height)"
							@click="openLightbox(image)"
						>
							<img
								:src="getImageUrl(image)"
								:alt="image.id"
								class="bg-gradient-to-br from-slate-100 via-slate-50 to-blue-50"
								:style="getGalleryImageStyle(image.id, image.height)"
								loading="lazy"
								@load="(e) => handleImageLoad(e, image.id)"
							>
							<!-- Top-left buttons (hover) -->
							<div
								class="absolute top-3 left-3 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity z-10"
							>
								<!-- Favorite button -->
								<button
									@click="(e) => handleToggleFavorite(e, image.id)"
									:disabled="savingFavorites.has(image.id)"
									:class="[
										'p-2 rounded-lg shadow-lg transition-all',
										isFavorite(image)
											? 'bg-red-500 text-white hover:bg-red-600'
											: 'bg-white/90 text-gray-600 hover:bg-white hover:text-red-500',
										savingFavorites.has(image.id) && 'opacity-50 cursor-not-allowed',
									]"
									:title="isFavorite(image) ? 'Remove from favorites' : 'Add to favorites'"
								>
									<Heart
										:size="16"
										:fill="isFavorite(image) ? 'currentColor' : 'none'"
									/>
								</button>
								<!-- Detail page button -->
								<RouterLink
									:to="`/image/${getDecodedId(image)}`"
									target="_blank"
									class="px-3 py-1.5 bg-black/70 hover:bg-black/90 text-white text-xs rounded-lg flex items-center gap-1.5"
									@click.stop
								>
									<TrendingUp :size="14"/>
									Details
								</RouterLink>
							</div>
							<!-- Ratings overlay -->
							<div class="absolute top-3 right-3 flex flex-col gap-1.5">
								<div
									v-if="image.moderations?.joycaption?.result !== undefined"
									:class="[
										getRatingColorClass(image.moderations.joycaption.result),
										'px-2 py-0.5 rounded text-white font-semibold text-xs shadow-lg',
									]"
								>
									Joy: {{ image.moderations.joycaption.result }}
								</div>
								<div
									v-if="image.moderations?.minicpm?.result !== undefined"
									:class="[
										getRatingColorClass(image.moderations.minicpm.result),
										'px-2 py-0.5 rounded text-white font-semibold text-xs shadow-lg',
									]"
								>
									Mini: {{ image.moderations.minicpm.result }}
								</div>
							</div>
						</div>
					</div>
				</div>
				<!-- Loading state (before images loaded) -->
				<div v-else class="flex flex-wrap justify-center gap-2">
					<div
						v-for="image in currentImages"
						:key="image.id"
						class="relative h-[480px] flex-shrink-0 group cursor-pointer overflow-hidden"
						:style="getGalleryImageContainerStyle(image.id, 480)"
						@click="openLightbox(image)"
					>
						<img
							:src="getImageUrl(image)"
							:alt="image.id"
							class="bg-gradient-to-br from-slate-100 via-slate-50 to-blue-50"
							:style="getGalleryImageStyle(image.id, 480)"
							loading="lazy"
							@load="(e) => handleImageLoad(e, image.id)"
						>
						<!-- Top-left buttons (hover) -->
						<div
							class="absolute top-3 left-3 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity z-10"
						>
							<!-- Favorite button -->
							<button
								@click="(e) => handleToggleFavorite(e, image.id)"
								:disabled="savingFavorites.has(image.id)"
								:class="[
									'p-2 rounded-lg shadow-lg transition-all',
									isFavorite(image)
										? 'bg-red-500 text-white hover:bg-red-600'
										: 'bg-white/90 text-gray-600 hover:bg-white hover:text-red-500',
									savingFavorites.has(image.id) && 'opacity-50 cursor-not-allowed',
								]"
								:title="isFavorite(image) ? 'Remove from favorites' : 'Add to favorites'"
							>
								<Heart
									:size="16"
									:fill="isFavorite(image) ? 'currentColor' : 'none'"
								/>
							</button>
							<!-- Detail page button -->
							<RouterLink
								:to="`/image/${getDecodedId(image)}`"
								target="_blank"
								class="px-3 py-1.5 bg-black/70 hover:bg-black/90 text-white text-xs rounded-lg flex items-center gap-1.5"
								@click.stop
							>
								<TrendingUp :size="14"/>
								Details
							</RouterLink>
						</div>
						<!-- Ratings overlay -->
						<div class="absolute top-3 right-3 flex flex-col gap-1.5">
							<div
								v-if="image.moderations?.joycaption?.result !== undefined"
								:class="[
									getRatingColorClass(image.moderations.joycaption.result),
									'px-2 py-0.5 rounded text-white font-semibold text-xs shadow-lg',
								]"
							>
								Joy: {{ image.moderations.joycaption.result }}
							</div>
							<div
								v-if="image.moderations?.minicpm?.result !== undefined"
								:class="[
									getRatingColorClass(image.moderations.minicpm.result),
									'px-2 py-0.5 rounded text-white font-semibold text-xs shadow-lg',
								]"
							>
								Mini: {{ image.moderations.minicpm.result }}
							</div>
						</div>
					</div>
				</div>
			</div>

			<!-- Image Grid - Increased density -->
			<div
				v-else
				class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-7 gap-3"
			>
				<ImageCard
					v-for="image in currentImages"
					:key="image.id"
					:image="image"
				/>
			</div>
		</template>

		<!-- Lightbox for gallery mode -->
		<ImageLightbox
			v-if="lightboxImage"
			:src="lightboxImage"
			:alt="lightboxAlt"
			@close="closeLightbox"
		/>
	</div>
</template>
