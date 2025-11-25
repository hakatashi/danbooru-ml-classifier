<script setup lang="ts">
import {doc, getDoc, getFirestore} from 'firebase/firestore';
import {computed, onMounted, ref, watch} from 'vue';
import type {SortOption} from '../composables/useImages';

const props = defineProps<{
	currentPage: number;
	canGoNext: boolean;
	canGoPrev: boolean;
}>();

const emit = defineEmits<{
	(e: 'sort-change', sort: SortOption): void;
	(e: 'page-change', page: number): void;
}>();

const sort = ref('minicpm-desc');
const totalCount = ref<number | null>(null);
const perPage = 20; // Images per page

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
	{
		value: 'joycaption-created-desc',
		label: 'JoyCaption Created (Newest First)',
		field: 'captions.joycaption.metadata.createdAt',
		direction: 'desc' as const,
	},
	{
		value: 'joycaption-created-asc',
		label: 'JoyCaption Created (Oldest First)',
		field: 'captions.joycaption.metadata.createdAt',
		direction: 'asc' as const,
	},
	{
		value: 'minicpm-created-desc',
		label: 'MiniCPM Created (Newest First)',
		field: 'captions.minicpm.metadata.createdAt',
		direction: 'desc' as const,
	},
	{
		value: 'minicpm-created-asc',
		label: 'MiniCPM Created (Oldest First)',
		field: 'captions.minicpm.metadata.createdAt',
		direction: 'asc' as const,
	},
];

function getSortOption(value: string): SortOption {
	const option = sortOptions.find((o) => o.value === value);
	return option
		? {field: option.field, direction: option.direction}
		: {field: 'moderations.joycaption.result', direction: 'desc'};
}

// Fetch total count from moderationStats for a given sort model
async function fetchTotalCount(sortValue: string) {
	const sortModel = sortValue.includes('joycaption') ? 'joycaption' : 'minicpm';
	try {
		const db = getFirestore();
		const statsDoc = await getDoc(doc(db, 'moderationStats', sortModel));
		if (statsDoc.exists()) {
			totalCount.value = statsDoc.data().count;
		}
	} catch (error) {
		console.error('Error fetching moderation stats:', error);
	}
}

// Initialize on mount
onMounted(async () => {
	await fetchTotalCount(sort.value);
});

// Update total count and emit sort change when sort changes
watch(
	sort,
	async (newSort) => {
		await fetchTotalCount(newSort);
		emit('sort-change', getSortOption(newSort));
	},
	{immediate: true},
);

// Calculate total pages
const totalPages = computed(() => {
	if (totalCount.value === null) return null;
	return Math.ceil(totalCount.value / perPage);
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
	<div
		class="sticky top-[72px] z-10 bg-white rounded-xl shadow-md p-3 sm:p-4 mb-6"
	>
		<div
			class="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3"
		>
			<!-- Top/Left: Sort dropdown -->
			<div class="flex items-center gap-2 min-w-0">
				<label class="text-sm font-medium text-gray-700 shrink-0">Sort:</label>
				<select
					v-model="sort"
					class="flex-1 sm:flex-none px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
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

			<!-- Bottom/Right: Pagination controls -->
			<div
				class="flex items-center justify-center sm:justify-end gap-2 sm:gap-3"
			>
				<button
					@click="prev"
					:disabled="!canGoPrev"
					:class="[
						'px-3 sm:px-4 py-1.5 rounded-lg text-sm font-medium transition-all shrink-0',
						canGoPrev
							? 'bg-blue-500 text-white hover:bg-blue-600 shadow-sm hover:shadow-md'
							: 'bg-gray-100 text-gray-400 cursor-not-allowed',
					]"
				>
					<span class="flex items-center gap-1">
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
						<span class="hidden sm:inline">Previous</span>
					</span>
				</button>

				<span
					class="text-gray-600 font-medium text-sm px-1 sm:px-2 whitespace-nowrap"
				>
					Page {{ currentPage + 1 }}
					<span v-if="totalPages !== null">/ {{ totalPages }}</span>
				</span>

				<button
					@click="next"
					:disabled="!canGoNext"
					:class="[
						'px-3 sm:px-4 py-1.5 rounded-lg text-sm font-medium transition-all shrink-0',
						canGoNext
							? 'bg-blue-500 text-white hover:bg-blue-600 shadow-sm hover:shadow-md'
							: 'bg-gray-100 text-gray-400 cursor-not-allowed',
					]"
				>
					<span class="flex items-center gap-1">
						<span class="hidden sm:inline">Next</span>
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
	</div>
</template>
