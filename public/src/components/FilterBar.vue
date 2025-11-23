<script setup lang="ts">
import { ref, watch } from 'vue'

defineProps<{
  totalCount: number
  filteredCount: number
}>()

const emit = defineEmits<{
  (e: 'filter-change', filters: { model: string; rating: string; sort: string }): void
}>()

const model = ref('all')
const rating = ref('all')
const sort = ref('rating-desc')

watch([model, rating, sort], () => {
  emit('filter-change', {
    model: model.value,
    rating: rating.value,
    sort: sort.value
  })
})
</script>

<template>
  <div class="bg-white rounded-xl shadow-md p-4 mb-6">
    <div class="flex flex-wrap items-center gap-4">
      <div class="flex items-center gap-2">
        <label class="text-sm font-medium text-gray-700">Model:</label>
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

      <div class="flex items-center gap-2">
        <label class="text-sm font-medium text-gray-700">Sort:</label>
        <select
          v-model="sort"
          class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        >
          <option value="rating-desc">Rating (High to Low)</option>
          <option value="rating-asc">Rating (Low to High)</option>
        </select>
      </div>

      <div class="ml-auto bg-blue-50 text-blue-700 px-4 py-2 rounded-lg text-sm font-medium">
        {{ filteredCount }} / {{ totalCount }} images
      </div>
    </div>
  </div>
</template>
