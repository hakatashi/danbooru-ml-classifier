<script setup lang="ts">
import type {User} from 'firebase/auth';
import {Heart, Image as ImageIcon, Lock, TrendingUp} from 'lucide-vue-next';
import {computed, onMounted, ref, watch} from 'vue';
import FilterBar from '../components/FilterBar.vue';
import ImageCard from '../components/ImageCard.vue';
import ImageLightbox from '../components/ImageLightbox.vue';
import {useFilterSync} from '../composables/useFilterSync';
import {useGallery} from '../composables/useGallery';
import {useImages} from '../composables/useImages';
import type {ImageDocument} from '../types';

const props = defineProps<{
	user: User | null;
}>();

// URL synchronization
const {sortValue, sort, page, filters, updateSort, updatePage, updateFilters} =
	useFilterSync();

// Image loading
const {
	loading,
	error,
	hasNextPage,
	hasPrevPage,
	loadPage,
	toggleFavorite,
	isFavorite,
} = useImages();

// Gallery functionality
const {
	imageAspectRatios,
	galleryRows,
	handleImageLoad,
	calculateGalleryRows,
	getGalleryImageContainerStyle,
	getGalleryImageStyle,
	resetGallery,
	handleResize,
} = useGallery();

// Local state
const currentImages = ref<ImageDocument[]>([]);
const galleryMode = ref(false);
const lightboxImage = ref<string | null>(null);
const lightboxAlt = ref<string>('');
const savingFavorites = ref<Set<string>>(new Set());

// Watch for user authentication and query parameter changes
watch(
	[() => props.user, sort, page, filters],
	async ([newUser, newSort, newPage, newFilters]) => {
		if (newUser && newSort) {
			const result = await loadPage(newSort, newPage, newFilters);
			currentImages.value = result.images;

			// Reset gallery state when images change
			resetGallery();
		}
	},
	{immediate: true, deep: true},
);

// Watch for gallery mode and images changes to recalculate rows
watch(
	[galleryMode, currentImages],
	([isGallery, images]) => {
		if (isGallery && images.length > 0) {
			// Collect aspect ratios from already loaded images
			setTimeout(() => {
				const imgElements = document.querySelectorAll('.gallery-image');
				imgElements.forEach((img) => {
					const imgEl = img as HTMLImageElement;
					if (imgEl.complete && imgEl.naturalWidth > 0) {
						const imageId = imgEl.alt;
						const aspectRatio = imgEl.naturalWidth / imgEl.naturalHeight;
						imageAspectRatios.value.set(imageId, aspectRatio);
					}
				});

				// If we have all aspect ratios, calculate rows
				if (imageAspectRatios.value.size === images.length) {
					calculateGalleryRows(images);
				}
			}, 100);
		}
	},
	{deep: true},
);

const canGoNext = computed(() => {
	return hasNextPage.value && currentImages.value.length > 0;
});

const canGoPrev = computed(() => {
	return hasPrevPage.value;
});

async function handlePageChange(newPage: number) {
	window.scrollTo({top: 0, behavior: 'smooth'});
	await updatePage(newPage);
}

function getImageUrl(image: ImageDocument): string {
	const IMAGE_BASE_URL =
		'https://matrix-images.hakatashi.com/hakataarchive/twitter/';
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

async function handleToggleFavorite(event: Event, imageId: string) {
	event.preventDefault();
	event.stopPropagation();

	if (savingFavorites.value.has(imageId)) return;

	savingFavorites.value.add(imageId);
	try {
		await toggleFavorite(imageId);
	} catch (err) {
		console.error('Failed to toggle favorite:', err);
	} finally {
		savingFavorites.value.delete(imageId);
	}
}

// Handle image load in gallery mode
function onImageLoad(event: Event, imageId: string) {
	handleImageLoad(event, imageId);

	// Automatically calculate rows when all aspect ratios are available
	if (
		galleryMode.value &&
		imageAspectRatios.value.size === currentImages.value.length
	) {
		calculateGalleryRows(currentImages.value);
	}
}

// Handle window resize
onMounted(() => {
	window.addEventListener('resize', () => {
		if (galleryMode.value) {
			handleResize(currentImages.value);
		}
	});
});
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
				:filters="filters"
				:sort="sortValue"
				:current-page="page"
				:can-go-next="canGoNext"
				:can-go-prev="canGoPrev"
				:gallery-mode="galleryMode"
				@update:filters="updateFilters"
				@update:sort="updateSort"
				@page-change="handlePageChange"
				@update:gallery-mode="(v) => (galleryMode = v)"
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
								class="gallery-image bg-gradient-to-br from-slate-100 via-slate-50 to-blue-50"
								:style="getGalleryImageStyle(image.id, image.height)"
								loading="lazy"
								@load="(e) => onImageLoad(e, image.id)"
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
									:title="
										isFavorite(image) ? 'Remove from favorites' : 'Add to favorites'
									"
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
							class="gallery-image bg-gradient-to-br from-slate-100 via-slate-50 to-blue-50"
							:style="getGalleryImageStyle(image.id, 480)"
							loading="lazy"
							@load="(e) => onImageLoad(e, image.id)"
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
								:title="
									isFavorite(image) ? 'Remove from favorites' : 'Add to favorites'
								"
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

			<!-- Image Grid -->
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
