<script setup lang="ts">
import { ref, computed } from 'vue'
import { collection, query, where, getDocs } from 'firebase/firestore'
import { type User } from 'firebase/auth'
import { db } from './firebase'
import type { ImageDocument } from './types'
import AuthButton from './components/AuthButton.vue'
import FilterBar from './components/FilterBar.vue'
import ImageCard from './components/ImageCard.vue'
import Pagination from './components/Pagination.vue'

const PAGE_SIZE = 20

const user = ref<User | null>(null)
const allImages = ref<ImageDocument[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const currentPage = ref(0)

const filters = ref({
  model: 'all',
  rating: 'all',
  sort: 'rating-desc'
})

function getModerationRating(image: ImageDocument, modelKey: string | null = null): number | null {
  if (!image.moderations) return null

  if (modelKey && image.moderations[modelKey]) {
    return image.moderations[modelKey].result
  }

  let maxRating: number | null = null
  for (const mod of Object.values(image.moderations)) {
    if (mod.result !== null && mod.result !== undefined) {
      if (maxRating === null || mod.result > maxRating) {
        maxRating = mod.result
      }
    }
  }
  return maxRating
}

const filteredImages = computed(() => {
  let result = allImages.value.filter(img => {
    // Model filter
    if (filters.value.model !== 'all') {
      if (!img.captions || !img.captions[filters.value.model]) {
        return false
      }
    }

    // Rating filter
    if (filters.value.rating !== 'all') {
      const rating = getModerationRating(img, filters.value.model !== 'all' ? filters.value.model : null)
      if (rating === null) return false

      const parts = filters.value.rating.split('-').map(Number)
      const min = parts[0] ?? 0
      const max = parts[1] ?? 10
      if (rating < min || rating > max) return false
    }

    return true
  })

  // Sort
  result.sort((a, b) => {
    const ratingA = getModerationRating(a) ?? -1
    const ratingB = getModerationRating(b) ?? -1

    if (filters.value.sort === 'rating-desc') {
      return ratingB - ratingA
    } else {
      return ratingA - ratingB
    }
  })

  return result
})

const totalPages = computed(() => Math.ceil(filteredImages.value.length / PAGE_SIZE))

const paginatedImages = computed(() => {
  const start = currentPage.value * PAGE_SIZE
  return filteredImages.value.slice(start, start + PAGE_SIZE)
})

async function loadImages() {
  if (!user.value) return

  loading.value = true
  error.value = null

  try {
    const imagesRef = collection(db, 'images')
    const q = query(imagesRef, where('captions', '!=', null))
    const snapshot = await getDocs(q)

    allImages.value = []
    snapshot.forEach((doc) => {
      const data = doc.data()
      if (data.captions && Object.keys(data.captions).length > 0) {
        allImages.value.push({
          id: doc.id,
          ...data
        } as ImageDocument)
      }
    })
  } catch (e) {
    console.error('Error loading images:', e)
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

function onAuthChange(newUser: User | null) {
  user.value = newUser
  if (newUser) {
    loadImages()
  } else {
    allImages.value = []
  }
}

function onFilterChange(newFilters: { model: string; rating: string; sort: string }) {
  filters.value = newFilters
  currentPage.value = 0
}

function onPageChange(page: number) {
  currentPage.value = page
  window.scrollTo({ top: 0, behavior: 'smooth' })
}
</script>

<template>
  <div class="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
    <!-- Header -->
    <header class="bg-white shadow-sm sticky top-0 z-50">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
        <div class="flex justify-between items-center">
          <h1 class="text-2xl font-bold text-gray-900">VLM Caption Viewer</h1>
          <AuthButton @auth-change="onAuthChange" />
        </div>
      </div>
    </header>

    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
      <!-- Auth Required -->
      <div v-if="!user" class="bg-white rounded-2xl shadow-lg p-12 text-center">
        <div class="max-w-md mx-auto">
          <div class="w-20 h-20 bg-blue-100 rounded-full flex items-center justify-center mx-auto mb-6">
            <svg class="w-10 h-10 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          </div>
          <h2 class="text-2xl font-bold text-gray-900 mb-3">Authentication Required</h2>
          <p class="text-gray-600 mb-6">Please login with your Google account to view the VLM results.</p>
        </div>
      </div>

      <!-- Main Content -->
      <template v-else>
        <FilterBar
          :total-count="allImages.length"
          :filtered-count="filteredImages.length"
          @filter-change="onFilterChange"
        />

        <!-- Loading State -->
        <div v-if="loading" class="flex justify-center items-center py-20">
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

        <!-- Image Grid -->
        <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
          <ImageCard
            v-for="image in paginatedImages"
            :key="image.id"
            :image="image"
          />
        </div>

        <!-- Pagination -->
        <Pagination
          v-if="filteredImages.length > 0"
          :current-page="currentPage"
          :total-pages="totalPages"
          @page-change="onPageChange"
        />
      </template>
    </main>
  </div>
</template>
