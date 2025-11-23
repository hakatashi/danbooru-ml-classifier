<script setup lang="ts">
defineProps<{
  src: string
  alt: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

function handleBackdropClick(event: MouseEvent) {
  if (event.target === event.currentTarget) {
    emit('close')
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    emit('close')
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-sm"
      @click="handleBackdropClick"
      @keydown="handleKeydown"
      tabindex="0"
      ref="backdrop"
    >
      <button
        @click="emit('close')"
        class="absolute top-4 right-4 p-2 text-white/80 hover:text-white transition-colors"
      >
        <svg class="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
      <img
        :src="src"
        :alt="alt"
        class="max-w-[95vw] max-h-[95vh] object-contain"
      />
    </div>
  </Teleport>
</template>
