<script setup lang="ts">
import { ref } from 'vue'
import { RouterView, RouterLink } from 'vue-router'
import { type User } from 'firebase/auth'
import AuthButton from './components/AuthButton.vue'
import { useImages } from './composables/useImages'

const user = ref<User | null>(null)
const { clearCache } = useImages()

function onAuthChange(newUser: User | null) {
  user.value = newUser
  if (!newUser) {
    clearCache()
  }
}
</script>

<template>
  <div class="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
    <!-- Header -->
    <header class="bg-white shadow-sm sticky top-0 z-40">
      <div class="w-full px-4 sm:px-6 lg:px-8 py-4">
        <div class="flex justify-between items-center">
          <RouterLink to="/" class="text-2xl font-bold text-gray-900 hover:text-blue-600 transition-colors">
            VLM Caption Viewer
          </RouterLink>
          <AuthButton @auth-change="onAuthChange" />
        </div>
      </div>
    </header>

    <main class="w-full px-4 sm:px-6 lg:px-8 py-6">
      <RouterView :user="user" />
    </main>
  </div>
</template>
