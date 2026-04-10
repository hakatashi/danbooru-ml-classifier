<script setup lang="ts">
import {onMounted, onUnmounted, ref} from 'vue';

defineProps<{
	src: string;
	alt: string;
}>();

const emit = defineEmits<(e: 'close') => void>();
const backdrop = ref<HTMLElement | null>(null);

// Enter fullscreen when mounted
onMounted(async () => {
	if (backdrop.value) {
		try {
			await backdrop.value.requestFullscreen();
		} catch (err) {
			console.error('Failed to enter fullscreen:', err);
		}
	}
});

// Exit fullscreen when unmounted
onUnmounted(() => {
	if (document.fullscreenElement) {
		document.exitFullscreen().catch((err) => {
			console.error('Failed to exit fullscreen:', err);
		});
	}
});

async function handleClose() {
	// Exit fullscreen before closing
	if (document.fullscreenElement) {
		try {
			await document.exitFullscreen();
		} catch (err) {
			console.error('Failed to exit fullscreen:', err);
		}
	}
	emit('close');
}

function handleBackdropClick(event: MouseEvent) {
	if (event.target === event.currentTarget) {
		handleClose();
	}
}

function handleImageClick(event: MouseEvent) {
	event.stopPropagation();
	handleClose();
}

function handleKeydown(event: KeyboardEvent) {
	if (event.key === 'Escape') {
		handleClose();
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
			<img
				:src="src"
				:alt="alt"
				class="w-screen h-screen object-contain cursor-pointer"
				@click="handleImageClick"
			>
		</div>
	</Teleport>
</template>
