<script setup lang="ts">
import type {User} from 'firebase/auth';
import {computed, ref, watch} from 'vue';
import FilterBar from '../components/FilterBar.vue';
import ImageCard from '../components/ImageCard.vue';
import Pagination from '../components/Pagination.vue';
import {type SortOption, useImages} from '../composables/useImages';
import type {ImageDocument} from '../types';

const props = defineProps<{
	user: User | null;
}>();

const PAGE_SIZE = 20;

const {images, loading, error, hasMore, loadImages, loadMore} = useImages();
const currentPage = ref(0);
const currentSort = ref<SortOption | null>(null);

const filters = ref({
	model: 'all',
	rating: 'all',
});

watch(
	() => props.user,
	(newUser) => {
		if (newUser && currentSort.value) {
			loadImages(currentSort.value);
		}
	},
	{immediate: true},
);

function getModerationRating(
	image: ImageDocument,
	modelKey: string,
): number | null {
	if (!image.moderations) return null;
	if (image.moderations[modelKey]) {
		return image.moderations[modelKey].result;
	}
	return null;
}

const filteredImages = computed(() => {
	return images.value.filter((img) => {
		// Model filter
		if (filters.value.model !== 'all') {
			if (!img.captions || !img.captions[filters.value.model]) {
				return false;
			}
		}

		// Rating filter (uses the current sort model for filtering)
		if (filters.value.rating !== 'all') {
			// Determine which model's rating to use based on current sort
			const sortModel = currentSort.value?.field.includes('joycaption')
				? 'joycaption'
				: 'minicpm';
			const rating = getModerationRating(img, sortModel);
			if (rating === null) return false;

			const parts = filters.value.rating.split('-').map(Number);
			const min = parts[0] ?? 0;
			const max = parts[1] ?? 10;
			if (rating < min || rating > max) return false;
		}

		return true;
	});
});

const totalPages = computed(() =>
	Math.ceil(filteredImages.value.length / PAGE_SIZE),
);

const paginatedImages = computed(() => {
	const start = currentPage.value * PAGE_SIZE;
	return filteredImages.value.slice(start, start + PAGE_SIZE);
});

async function onSortChange(sort: SortOption) {
	currentSort.value = sort;
	currentPage.value = 0;
	if (props.user) {
		await loadImages(sort);
	}
}

function onFilterChange(newFilters: {model: string; rating: string}) {
	filters.value = newFilters;
	currentPage.value = 0;
}

async function onPageChange(page: number) {
	currentPage.value = page;
	window.scrollTo({top: 0, behavior: 'smooth'});

	// Load more if we're near the end
	const neededImages = (page + 2) * PAGE_SIZE;
	if (neededImages > images.value.length && hasMore.value && !loading.value) {
		await loadMore();
	}
}

async function handleLoadMore() {
	await loadMore();
}
</script>

<template>
  <div>
    <!-- Auth Required -->
    <div v-if="!user" class="bg-white rounded-2xl shadow-lg p-12 text-center max-w-md mx-auto">
      <div class="w-20 h-20 bg-blue-100 rounded-full flex items-center justify-center mx-auto mb-6">
        <svg class="w-10 h-10 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
        </svg>
      </div>
      <h2 class="text-2xl font-bold text-gray-900 mb-3">Authentication Required</h2>
      <p class="text-gray-600 mb-6">Please login with your Google account to view the VLM results.</p>
    </div>

    <!-- Main Content -->
    <template v-else>
      <FilterBar
        :total-count="filteredImages.length"
        @sort-change="onSortChange"
        @filter-change="onFilterChange"
      />

      <!-- Loading State -->
      <div v-if="loading && images.length === 0" class="flex justify-center items-center py-20">
        <div class="flex flex-col items-center gap-4">
          <div class="animate-spin rounded-full h-12 w-12 border-4 border-blue-500 border-t-transparent"></div>
          <p class="text-gray-600">Loading images...</p>
        </div>
      </div>

      <!-- Error State -->
      <div v-else-if="error" class="bg-red-50 border border-red-200 rounded-xl p-6 text-center">
        <p class="text-red-700">Error: {{ error }}</p>
      </div>

      <!-- Empty State -->
      <div v-else-if="filteredImages.length === 0" class="bg-white rounded-xl shadow-md p-12 text-center">
        <div class="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
          <svg class="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
        </div>
        <p class="text-gray-600">No images found</p>
      </div>

      <!-- Image Grid - Full Width -->
      <template v-else>
        <!-- Top Pagination -->
        <Pagination
          :current-page="currentPage"
          :total-pages="totalPages"
          @page-change="onPageChange"
        />

        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-4">
          <ImageCard
            v-for="image in paginatedImages"
            :key="image.id"
            :image="image"
          />
        </div>

        <!-- Bottom Pagination -->
        <Pagination
          :current-page="currentPage"
          :total-pages="totalPages"
          @page-change="onPageChange"
        />

        <!-- Load More Button -->
        <div v-if="hasMore" class="flex justify-center mt-4">
          <button
            @click="handleLoadMore"
            :disabled="loading"
            class="px-6 py-2 bg-blue-500 hover:bg-blue-600 disabled:bg-blue-300 text-white rounded-lg font-medium transition-colors"
          >
            <span v-if="loading" class="flex items-center gap-2">
              <div class="animate-spin rounded-full h-4 w-4 border-2 border-white border-t-transparent"></div>
              Loading...
            </span>
            <span v-else>Load More</span>
          </button>
        </div>
      </template>
    </template>
  </div>
</template>
