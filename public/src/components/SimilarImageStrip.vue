<script setup lang="ts">
import {Heart} from 'lucide-vue-next';
import {ref} from 'vue';
import {getImageUrl, type SimilarImage} from '../api/mlApi';
import {useImages} from '../composables/useImages';

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

const {toggleFavorite, isFavorite} = useImages();
const savingFavoriteIds = ref<Set<string>>(new Set());

async function handleToggleFavorite(event: Event, imageId: string) {
	event.stopPropagation();
	event.preventDefault();
	if (savingFavoriteIds.value.has(imageId)) return;
	savingFavoriteIds.value.add(imageId);
	try {
		await toggleFavorite(imageId);
	} catch (err) {
		console.error('Failed to toggle favorite:', err);
	} finally {
		savingFavoriteIds.value.delete(imageId);
	}
}

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
			<button
				type="button"
				@click="(e) => handleToggleFavorite(e, sim.id)"
				:disabled="savingFavoriteIds.has(sim.id)"
				:class="[
					'absolute top-1.5 left-1.5 p-1.5 rounded-md shadow-lg transition-all z-10',
					isFavorite(sim.id)
						? 'bg-red-500 text-white hover:bg-red-600'
						: 'bg-white/90 text-gray-600 hover:bg-white hover:text-red-500',
					savingFavoriteIds.has(sim.id) && 'opacity-50 cursor-not-allowed',
					!isFavorite(sim.id) && 'opacity-0 group-hover:opacity-100',
				]"
				:title="isFavorite(sim.id) ? 'Remove from favorites' : 'Add to favorites'"
			>
				<Heart
					:size="14"
					:fill="isFavorite(sim.id) ? 'currentColor' : 'none'"
				/>
			</button>
			<span
				class="absolute bottom-1 right-1 px-1.5 py-0.5 bg-black/70 text-white text-xs rounded font-mono opacity-0 group-hover:opacity-100 transition-opacity"
			>
				{{ (sim.similarity * 100).toFixed(1) }}%
			</span>
		</RouterLink>
	</div>
</template>
