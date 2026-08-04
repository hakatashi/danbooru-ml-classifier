<script setup lang="ts">
import {Eye, EyeOff} from 'lucide-vue-next';
import {computed} from 'vue';
import {usePageViews} from '../composables/usePageViews';

const props = defineProps<{
	date: string;
	sortField: string;
	page: number;
	imageIds: string[];
}>();

const {isPageViewed, isPending, markViewed, unmarkViewed} = usePageViews();

const key = computed(() => ({
	date: props.date,
	sortField: props.sortField,
	page: props.page,
}));
const isViewed = computed(() => isPageViewed(key.value));
const isSaving = computed(() => isPending(key.value));

async function handleClick() {
	if (isSaving.value) return;
	try {
		if (isViewed.value) {
			await unmarkViewed(key.value);
		} else {
			await markViewed(key.value, props.imageIds);
		}
	} catch (error) {
		console.error('Failed to toggle page-viewed mark:', error);
	}
}
</script>

<template>
	<button
		type="button"
		:disabled="isSaving"
		:class="[
			'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-sm font-medium transition-all',
			isViewed
				? 'bg-green-500 text-white border-green-500 hover:bg-green-600'
				: 'bg-white text-gray-600 border-gray-300 hover:border-green-400 hover:text-green-600',
			isSaving && 'opacity-50 cursor-not-allowed',
		]"
		@click="handleClick"
	>
		<Eye v-if="isViewed" :size="16" />
		<EyeOff v-else :size="16" />
		<span>{{ isViewed ? 'Page viewed' : 'Mark page as viewed' }}</span>
	</button>
</template>
