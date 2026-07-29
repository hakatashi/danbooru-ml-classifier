<script setup lang="ts">
import {Heart} from 'lucide-vue-next';
import {computed} from 'vue';
import {useFavorites} from '../composables/useFavorites';

const props = withDefaults(
	defineProps<{
		imageId: string;
		category?: string;
		size?: number;
		variant?: 'overlay' | 'inline';
		hideWhenUnfavorited?: boolean;
	}>(),
	{
		category: 'Uncategorized',
		size: 20,
		variant: 'overlay',
		hideWhenUnfavorited: false,
	},
);

const {toggleFavorite, isFavorite, isPending} = useFavorites();

const isFavorited = computed(() => isFavorite(props.imageId, props.category));
const isSaving = computed(() => isPending(props.imageId));

async function handleClick(event: Event) {
	event.preventDefault();
	event.stopPropagation();
	if (isSaving.value) return;
	try {
		await toggleFavorite(props.imageId, props.category);
	} catch (error) {
		console.error('Failed to toggle favorite:', error);
	}
}
</script>

<template>
	<button
		type="button"
		:disabled="isSaving"
		:title="isFavorited ? 'Remove from favorites' : 'Add to favorites'"
		:class="[
			'inline-flex items-center gap-1.5 transition-all',
			variant === 'overlay'
				? [
						'p-2 rounded-lg shadow-lg',
						isFavorited
							? 'bg-red-500 text-white hover:bg-red-600'
							: 'bg-white/90 text-gray-600 hover:bg-white hover:text-red-500',
					]
				: [
						'px-3 py-1.5 rounded-lg border text-sm font-medium',
						isFavorited
							? 'bg-red-500 text-white border-red-500 hover:bg-red-600'
							: 'bg-white text-gray-600 border-gray-300 hover:border-red-400 hover:text-red-500',
					],
			isSaving && 'opacity-50 cursor-not-allowed',
			hideWhenUnfavorited && !isFavorited && 'opacity-0 group-hover:opacity-100',
		]"
		@click="handleClick"
	>
		<Heart :size="size" :fill="isFavorited ? 'currentColor' : 'none'" />
		<span v-if="variant === 'inline'"
			>{{ isFavorited ? 'Favorited' : 'Favorite' }}</span
		>
	</button>
</template>
