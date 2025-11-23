<script setup lang="ts">
import {computed, ref} from 'vue';
import {RouterLink} from 'vue-router';
import type {ImageDocument} from '../types';
import ImageLightbox from './ImageLightbox.vue';
import ThinkBlock from './ThinkBlock.vue';

const props = defineProps<{
	image: ImageDocument;
}>();

const IMAGE_BASE_URL =
	'https://matrix.hakatashi.com/images/hakataarchive/twitter/';

const showLightbox = ref(false);
const models = computed(() => Object.keys(props.image.captions || {}));
const activeModel = ref(models.value[0] || '');

const filename = computed(() => {
	return props.image.key ? props.image.key.split('/').pop() : props.image.id;
});

const imageUrl = computed(() => IMAGE_BASE_URL + filename.value);

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
</script>

<template>
	<div
		class="bg-white rounded-xl shadow-md overflow-hidden hover:shadow-lg transition-shadow"
	>
		<div class="relative bg-black flex items-center justify-center max-h-96">
			<img
				:src="imageUrl"
				:alt="filename"
				class="max-w-full max-h-96 object-contain cursor-pointer hover:opacity-90 transition-opacity"
				loading="lazy"
				@click="openLightbox"
				@error="($event.target as HTMLImageElement).src = 'data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22200%22 height=%22200%22><text x=%2250%%22 y=%2250%%22 text-anchor=%22middle%22 fill=%22white%22>Image not found</text></svg>'"
			>
			<!-- Stacked ratings -->
			<div class="absolute top-3 right-3 flex flex-col gap-1.5">
				<div
					v-if="joycaptionRating !== null"
					:class="[getRatingColorClass(joycaptionRating), 'px-2 py-0.5 rounded text-white font-semibold text-xs shadow-lg']"
				>
					Joy: {{ joycaptionRating }}
				</div>
				<div
					v-if="minicpmRating !== null"
					:class="[getRatingColorClass(minicpmRating), 'px-2 py-0.5 rounded text-white font-semibold text-xs shadow-lg']"
				>
					Mini: {{ minicpmRating }}
				</div>
			</div>
		</div>

		<div class="p-4">
			<div class="flex justify-between items-center text-xs text-gray-500 mb-3">
				<RouterLink
					:to="{ name: 'image-detail', params: { id: image.id } }"
					class="font-mono truncate max-w-[200px] hover:text-blue-600 hover:underline"
				>
					{{ filename }}
				</RouterLink>
				<span class="bg-gray-100 px-2 py-1 rounded"
					>{{ image.type || 'unknown' }}</span
				>
			</div>

			<div class="flex gap-2 mb-3">
				<button
					v-for="model in models"
					:key="model"
					:class="[
            'px-3 py-1.5 text-xs font-medium rounded-lg transition-colors',
            model === activeModel
              ? 'bg-blue-500 text-white'
              : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
          ]"
					@click="selectModel(model)"
				>
					{{ model }}
				</button>
			</div>

			<div class="bg-gray-50 rounded-lg p-3 max-h-48 overflow-y-auto">
				<ThinkBlock
					:text="currentCaption"
					class="text-sm text-gray-700 leading-relaxed"
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
