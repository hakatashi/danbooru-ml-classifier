<script setup lang="ts">
import {ref, watch} from 'vue';
import type {SortOption} from '../composables/useImages';

const props = defineProps<{
	totalCount: number;
	currentPage: number;
	canGoNext: boolean;
	canGoPrev: boolean;
}>();

const emit = defineEmits<{
	(e: 'sort-change', sort: SortOption): void;
	(e: 'filter-change', filters: {model: string; rating: string}): void;
	(e: 'page-change', page: number): void;
}>();

const model = ref('all');
const rating = ref('all');
const sort = ref('minicpm-desc');

const sortOptions = [
	{
		value: 'joycaption-desc',
		label: 'JoyCaption Rating (High to Low)',
		field: 'moderations.joycaption.result',
		direction: 'desc' as const,
	},
	{
		value: 'joycaption-asc',
		label: 'JoyCaption Rating (Low to High)',
		field: 'moderations.joycaption.result',
		direction: 'asc' as const,
	},
	{
		value: 'minicpm-desc',
		label: 'MiniCPM Rating (High to Low)',
		field: 'moderations.minicpm.result',
		direction: 'desc' as const,
	},
	{
		value: 'minicpm-asc',
		label: 'MiniCPM Rating (Low to High)',
		field: 'moderations.minicpm.result',
		direction: 'asc' as const,
	},
];

function getSortOption(value: string): SortOption {
	const option = sortOptions.find((o) => o.value === value);
	return option
		? {field: option.field, direction: option.direction}
		: {field: 'moderations.joycaption.result', direction: 'desc'};
}

// Emit sort change
watch(
	sort,
	(newSort) => {
		emit('sort-change', getSortOption(newSort));
	},
	{immediate: true},
);

// Emit filter change
watch([model, rating], () => {
	emit('filter-change', {
		model: model.value,
		rating: rating.value,
	});
});

function prev() {
	if (props.canGoPrev) {
		emit('page-change', props.currentPage - 1);
	}
}

function next() {
	if (props.canGoNext) {
		emit('page-change', props.currentPage + 1);
	}
}
</script>

<template>
	<div class="sticky top-0 z-10 bg-white rounded-xl shadow-md p-4 mb-6">
		<div class="flex flex-wrap items-center gap-4 mb-3">
			<div class="flex items-center gap-2">
				<label class="text-sm font-medium text-gray-700">Sort:</label>
				<select
					v-model="sort"
					class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
				>
					<option
						v-for="option in sortOptions"
						:key="option.value"
						:value="option.value"
					>
						{{ option.label }}
					</option>
				</select>
			</div>

			<div class="flex items-center gap-2">
				<label class="text-sm font-medium text-gray-700">Filter Model:</label>
				<select
					v-model="model"
					class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
				>
					<option value="all">All Models</option>
					<option value="joycaption">JoyCaption</option>
					<option value="minicpm">MiniCPM</option>
				</select>
			</div>

			<div class="flex items-center gap-2">
				<label class="text-sm font-medium text-gray-700">Rating:</label>
				<select
					v-model="rating"
					class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
				>
					<option value="all">All Ratings</option>
					<option value="0-2">0-2 (Safe)</option>
					<option value="3-4">3-4 (Slightly Suggestive)</option>
					<option value="5-6">5-6 (Sensitive)</option>
					<option value="7-8">7-8 (Adult)</option>
					<option value="9-10">9-10 (Explicit)</option>
				</select>
			</div>

			<div
				class="ml-auto bg-blue-50 text-blue-700 px-4 py-2 rounded-lg text-sm font-medium"
			>
				{{ totalCount }}images
			</div>
		</div>

		<!-- Pagination Controls -->
		<div
			class="flex justify-center items-center gap-3 pt-3 border-t border-gray-200"
		>
			<button
				@click="prev"
				:disabled="!canGoPrev"
				:class="[
					'px-4 py-1.5 rounded-lg text-sm font-medium transition-all',
					canGoPrev
						? 'bg-blue-500 text-white hover:bg-blue-600 shadow-sm hover:shadow-md'
						: 'bg-gray-100 text-gray-400 cursor-not-allowed',
				]"
			>
				<span class="flex items-center gap-1.5">
					<svg
						class="w-4 h-4"
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

			<span class="text-gray-600 font-medium text-sm px-3">
				Page {{ currentPage + 1 }}
			</span>

			<button
				@click="next"
				:disabled="!canGoNext"
				:class="[
					'px-4 py-1.5 rounded-lg text-sm font-medium transition-all',
					canGoNext
						? 'bg-blue-500 text-white hover:bg-blue-600 shadow-sm hover:shadow-md'
						: 'bg-gray-100 text-gray-400 cursor-not-allowed',
				]"
			>
				<span class="flex items-center gap-1.5">
					Next
					<svg
						class="w-4 h-4"
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
	</div>
</template>
