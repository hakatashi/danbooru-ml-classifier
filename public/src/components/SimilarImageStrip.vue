<script setup lang="ts">
import {getImageUrl, type SimilarImage} from '../api/mlApi';
import FavoriteButton from './FavoriteButton.vue';

withDefaults(
	defineProps<{
		images: SimilarImage[];
		loading: boolean;
		error: string | null;
		imageHeight?: string;
		loadingMessage?: string;
		emptyMessage?: string;
	}>(),
	{
		imageHeight: 'h-64',
		loadingMessage: 'Searching for similar images...',
		emptyMessage: 'No similar images found.',
	},
);

const emit = defineEmits<{
	hoverEnter: [sim: SimilarImage];
	hoverLeave: [];
}>();

function onWheel(e: WheelEvent) {
	const el = e.currentTarget as HTMLElement;
	el.scrollLeft += e.deltaY;
}
</script>

<template>
	<div
		v-if="loading"
		class="flex items-center gap-2 text-sm text-gray-500 py-4"
	>
		<div
			class="animate-spin rounded-full h-4 w-4 border-2 border-blue-500 border-t-transparent"
		/>
		{{ loadingMessage }}
	</div>
	<p v-else-if="error" class="text-sm text-red-500 py-2">{{ error }}</p>
	<p v-else-if="images.length === 0" class="text-sm text-gray-400 py-2">
		{{ emptyMessage }}
	</p>
	<div v-else class="flex gap-2 overflow-x-auto pb-2" @wheel.prevent="onWheel">
		<RouterLink
			v-for="sim in images"
			:key="sim.id"
			:to="`/daily/image/${sim.id}`"
			class="flex-shrink-0 relative group"
			:title="`Similarity: ${(sim.similarity * 100).toFixed(1)}%`"
			@mouseenter="emit('hoverEnter', sim)"
			@mouseleave="emit('hoverLeave')"
		>
			<img
				:src="getImageUrl(sim, true)"
				:alt="sim.id"
				:class="['w-auto object-cover rounded-lg bg-gray-100', imageHeight]"
				loading="lazy"
			>
			<FavoriteButton
				:image-id="sim.id"
				:size="14"
				variant="overlay"
				hide-when-unfavorited
				class="absolute top-1.5 left-1.5 z-10"
			/>
			<span
				class="absolute bottom-1 right-1 px-1.5 py-0.5 bg-black/70 text-white text-xs rounded font-mono opacity-0 group-hover:opacity-100 transition-opacity"
			>
				{{ (sim.similarity * 100).toFixed(1) }}%
			</span>
		</RouterLink>
	</div>
</template>
