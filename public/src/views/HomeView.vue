<script setup lang="ts">
import type {User} from 'firebase/auth';
import {computed, ref, watch} from 'vue';
import FilterBar from '../components/FilterBar.vue';
import ImageCard from '../components/ImageCard.vue';
import {type SortOption, useImages} from '../composables/useImages';
import type {ImageDocument} from '../types';

const props = defineProps<{
	user: User | null;
}>();

const {loading, error, hasNextPage, hasPrevPage, loadPage} = useImages();
const currentPage = ref(0);
const currentSort = ref<SortOption | null>(null);
const currentImages = ref<ImageDocument[]>([]);

watch(
	() => props.user,
	async (newUser) => {
		if (newUser && currentSort.value) {
			const result = await loadPage(currentSort.value, 0);
			currentImages.value = result.images;
			currentPage.value = result.page;
		}
	},
	{immediate: true},
);

const canGoNext = computed(() => {
	return hasNextPage.value && currentImages.value.length > 0;
});

const canGoPrev = computed(() => {
	return hasPrevPage.value;
});

async function onSortChange(sort: SortOption) {
	currentSort.value = sort;
	currentPage.value = 0;
	if (props.user) {
		const result = await loadPage(sort, 0);
		currentImages.value = result.images;
		currentPage.value = result.page;
	}
}

async function onPageChange(page: number) {
	if (!currentSort.value || !props.user) return;

	window.scrollTo({top: 0, behavior: 'smooth'});

	const direction = page > currentPage.value ? 'forward' : 'backward';
	const result = await loadPage(currentSort.value, page, direction);
	currentImages.value = result.images;
	currentPage.value = result.page;
}
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
				<svg
					class="w-10 h-10 text-blue-500"
					fill="none"
					stroke="currentColor"
					viewBox="0 0 24 24"
				>
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="2"
						d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
					/>
				</svg>
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
				:current-page="currentPage"
				:can-go-next="canGoNext"
				:can-go-prev="canGoPrev"
				@sort-change="onSortChange"
				@page-change="onPageChange"
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
					<svg
						class="w-8 h-8 text-gray-400"
						fill="none"
						stroke="currentColor"
						viewBox="0 0 24 24"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							stroke-width="2"
							d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
						/>
					</svg>
				</div>
				<p class="text-gray-600">No images found</p>
			</div>

			<!-- Image Grid - Full Width -->
			<div
				v-else
				class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-4"
			>
				<ImageCard
					v-for="image in currentImages"
					:key="image.id"
					:image="image"
				/>
			</div>
		</template>
	</div>
</template>
