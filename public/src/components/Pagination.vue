<script setup lang="ts">
import {computed} from 'vue';

const props = defineProps<{
	currentPage: number;
	totalPages: number;
}>();

const emit = defineEmits<(e: 'page-change', page: number) => void>();

const canGoPrev = computed(() => props.currentPage > 0);
const canGoNext = computed(() => props.currentPage < props.totalPages - 1);

function prev() {
	if (canGoPrev.value) {
		emit('page-change', props.currentPage - 1);
	}
}

function next() {
	if (canGoNext.value) {
		emit('page-change', props.currentPage + 1);
	}
}
</script>

<template>
	<div class="flex justify-center items-center gap-4 py-6">
		<button
			@click="prev"
			:disabled="!canGoPrev"
			:class="[
        'px-6 py-2.5 rounded-lg font-medium transition-all',
        canGoPrev
          ? 'bg-white text-gray-700 shadow-md hover:shadow-lg hover:bg-gray-50'
          : 'bg-gray-100 text-gray-400 cursor-not-allowed'
      ]"
		>
			<span class="flex items-center gap-2">
				<svg
					class="w-5 h-5"
					fill="none"
					stroke="currentColor"
					viewBox="0 0 24 24"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M15 19l-7-7 7-7"
					/>
				</svg>
				Previous
			</span>
		</button>

		<span class="text-gray-600 font-medium px-4">
			Page {{ currentPage + 1 }}of {{ totalPages || 1 }}
		</span>

		<button
			@click="next"
			:disabled="!canGoNext"
			:class="[
        'px-6 py-2.5 rounded-lg font-medium transition-all',
        canGoNext
          ? 'bg-white text-gray-700 shadow-md hover:shadow-lg hover:bg-gray-50'
          : 'bg-gray-100 text-gray-400 cursor-not-allowed'
      ]"
		>
			<span class="flex items-center gap-2">
				Next
				<svg
					class="w-5 h-5"
					fill="none"
					stroke="currentColor"
					viewBox="0 0 24 24"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M9 5l7 7-7 7"
					/>
				</svg>
			</span>
		</button>
	</div>
</template>
