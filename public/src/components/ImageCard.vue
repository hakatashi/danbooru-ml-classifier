<script setup lang="ts">
import {Heart} from 'lucide-vue-next';
import {computed, ref} from 'vue';
import {RouterLink} from 'vue-router';
import {useImages} from '../composables/useImages';
import type {ImageDocument} from '../types';
import ImageLightbox from './ImageLightbox.vue';
import ThinkBlock from './ThinkBlock.vue';

const props = defineProps<{
	image: ImageDocument;
}>();

const {toggleFavorite, isFavorite} = useImages();
const isFavorited = computed(() => isFavorite(props.image));
const isSaving = ref(false);

const IMAGE_BASE_URL =
	'https://matrix-images.hakatashi.com/hakataarchive/twitter/';

const showLightbox = ref(false);
const models = computed(() => Object.keys(props.image.captions || {}));
const activeModel = ref(models.value[0] || '');

const filename = computed(() => {
	return props.image.key ? props.image.key.split('/').pop() : props.image.id;
});

const imageUrl = computed(() => IMAGE_BASE_URL + filename.value);

// Decode the document ID to use as URL path (e.g., "twitter%2Fimage.jpg" -> "twitter/image.jpg")
const decodedId = computed(() => {
	return decodeURIComponent(props.image.id);
});

const currentCaption = computed(() => {
	return (
		props.image.captions?.[activeModel.value]?.caption || 'No caption available'
	);
});

const joycaptionRating = computed(() => {
	return props.image.moderations?.['joycaption']?.result ?? null;
});

const minicpmRating = computed(() => {
	return props.image.moderations?.['minicpm']?.result ?? null;
});

function getRatingColorClass(rating: number | null): string {
	if (rating === null) return 'bg-gray-500';
	if (rating <= 2) return 'bg-green-500';
	if (rating <= 4) return 'bg-lime-500';
	if (rating <= 6) return 'bg-orange-500';
	if (rating <= 8) return 'bg-red-500';
	return 'bg-purple-500';
}

function selectModel(model: string) {
	activeModel.value = model;
}

function openLightbox() {
	showLightbox.value = true;
}

async function handleToggleFavorite(event: Event) {
	event.preventDefault();
	event.stopPropagation();

	if (isSaving.value) return;

	isSaving.value = true;
	try {
		await toggleFavorite(props.image.id);
	} catch (error) {
		console.error('Failed to toggle favorite:', error);
	} finally {
		isSaving.value = false;
	}
}
</script>

<template>
	<div
		class="bg-white rounded-xl shadow-md overflow-hidden hover:shadow-lg transition-shadow"
	>
		<!-- Fixed height image area with gradient background -->
		<div
			class="relative h-64 flex items-center justify-center bg-gradient-to-br from-slate-100 via-slate-50 to-blue-50 overflow-hidden group"
		>
			<img
				:src="imageUrl"
				:alt="filename"
				class="max-w-full max-h-full object-contain cursor-pointer hover:opacity-90 transition-opacity"
				loading="lazy"
				@click="openLightbox"
				@error="($event.target as HTMLImageElement).src = 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22200%22 height=%22200%22><text x=%2250%%22 y=%2250%%22 text-anchor=%22middle%22 fill=%22%23999%22>Image not found</text></svg>'"
			>
			<!-- Favorite button (top-left) -->
			<button
				@click="handleToggleFavorite"
				:disabled="isSaving"
				:class="[
					'absolute top-3 left-3 p-2 rounded-lg shadow-lg transition-all',
					isFavorited
						? 'bg-red-500 text-white hover:bg-red-600'
						: 'bg-white/90 text-gray-600 hover:bg-white hover:text-red-500',
					isSaving && 'opacity-50 cursor-not-allowed',
				]"
				:title="isFavorited ? 'Remove from favorites' : 'Add to favorites'"
			>
				<Heart :size="20" :fill="isFavorited ? 'currentColor' : 'none'" />
			</button>
			<!-- Stacked ratings -->
			<div class="absolute top-3 right-3 flex flex-col gap-1.5">
				<div
					v-if="joycaptionRating !== null"
					:class="[
						getRatingColorClass(joycaptionRating),
						'px-2 py-0.5 rounded text-white font-semibold text-xs shadow-lg',
					]"
				>
					Joy: {{ joycaptionRating }}
				</div>
				<div
					v-if="minicpmRating !== null"
					:class="[
						getRatingColorClass(minicpmRating),
						'px-2 py-0.5 rounded text-white font-semibold text-xs shadow-lg',
					]"
				>
					Mini: {{ minicpmRating }}
				</div>
			</div>
		</div>

		<div class="p-3">
			<div class="flex justify-between items-center text-xs text-gray-500 mb-2">
				<RouterLink
					:to="`/image/${decodedId}`"
					target="_blank"
					class="font-mono truncate max-w-[60%] hover:text-blue-600 hover:underline"
				>
					{{ filename }}
				</RouterLink>
				<span class="bg-gray-100 px-2 py-0.5 rounded text-xs"
					>{{ image.type || 'unknown' }}</span
				>
			</div>

			<div class="flex gap-1.5 mb-2 flex-wrap">
				<button
					v-for="model in models"
					:key="model"
					:class="[
						'px-2 py-1 text-xs font-medium rounded transition-colors',
						model === activeModel
							? 'bg-blue-500 text-white'
							: 'bg-gray-100 text-gray-700 hover:bg-gray-200',
					]"
					@click="selectModel(model)"
				>
					{{ model }}
				</button>
			</div>

			<!-- Caption preview with ellipsis (no scroll) -->
			<div class="bg-gray-50 rounded-lg p-2">
				<ThinkBlock
					:text="currentCaption"
					class="text-xs text-gray-700 leading-relaxed line-clamp-3"
				/>
			</div>
		</div>

		<ImageLightbox
			v-if="showLightbox"
			:src="imageUrl"
			:alt="filename || ''"
			@close="showLightbox = false"
		/>
	</div>
</template>
