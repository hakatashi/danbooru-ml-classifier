<script setup lang="ts">
import { ref, computed } from 'vue'

const props = defineProps<{
  text: string
}>()

const expandedBlocks = ref<Set<number>>(new Set())

interface TextPart {
  type: 'text' | 'think'
  content: string
  index: number
}

const parsedContent = computed<TextPart[]>(() => {
  const parts: TextPart[] = []
  const regex = /<think>([\s\S]*?)<\/think>/gi
  let lastIndex = 0
  let match
  let thinkIndex = 0

  const text = props.text || ''

  while ((match = regex.exec(text)) !== null) {
    // Add text before the think block
    if (match.index > lastIndex) {
      parts.push({
        type: 'text',
        content: text.slice(lastIndex, match.index),
        index: -1
      })
    }

    // Add the think block
    parts.push({
      type: 'think',
      content: (match[1] ?? '').trim(),
      index: thinkIndex++
    })

    lastIndex = match.index + match[0].length
  }

  // Add remaining text
  if (lastIndex < text.length) {
    parts.push({
      type: 'text',
      content: text.slice(lastIndex),
      index: -1
    })
  }

  return parts
})

function toggleBlock(index: number) {
  if (expandedBlocks.value.has(index)) {
    expandedBlocks.value.delete(index)
  } else {
    expandedBlocks.value.add(index)
  }
  expandedBlocks.value = new Set(expandedBlocks.value)
}

function isExpanded(index: number) {
  return expandedBlocks.value.has(index)
}
</script>

<template>
  <div class="whitespace-pre-wrap">
    <template v-for="(part, i) in parsedContent" :key="i">
      <span v-if="part.type === 'text'">{{ part.content }}</span>
      <div v-else class="my-2">
        <button
          @click="toggleBlock(part.index)"
          class="inline-flex items-center gap-1.5 px-2 py-1 text-xs font-medium text-purple-700 bg-purple-100 hover:bg-purple-200 rounded-md transition-colors"
        >
          <svg
            :class="['w-3 h-3 transition-transform', isExpanded(part.index) ? 'rotate-90' : '']"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
          </svg>
          <span>Thinking...</span>
          <span class="text-purple-500">({{ part.content.length }} chars)</span>
        </button>
        <div
          v-if="isExpanded(part.index)"
          class="mt-2 p-3 bg-purple-50 border-l-4 border-purple-300 rounded-r-lg text-sm text-purple-900"
        >
          {{ part.content }}
        </div>
      </div>
    </template>
  </div>
</template>
