<script setup lang="ts">
import {ChevronLeft, ChevronRight, X} from 'lucide-vue-next';
import {computed, onMounted, onUnmounted, ref} from 'vue';

const props = withDefaults(
	defineProps<{
		src: string;
		alt: string;
		canPrev?: boolean;
		canNext?: boolean;
	}>(),
	{
		canPrev: false,
		canNext: false,
	},
);

const emit = defineEmits<{
	close: [];
	prev: [];
	next: [];
}>();
const backdrop = ref<HTMLElement | null>(null);
const hasNav = computed(() => props.canPrev || props.canNext);

// Enter fullscreen when mounted
onMounted(async () => {
	if (backdrop.value) {
		backdrop.value.focus();
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
	} else if (event.key === 'ArrowLeft' && props.canPrev) {
		emit('prev');
	} else if (event.key === 'ArrowRight' && props.canNext) {
		emit('next');
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

			<button
				v-if="hasNav"
				type="button"
				class="absolute top-4 right-4 p-2 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors"
				title="Close"
				@click.stop="handleClose"
			>
				<X :size="24" />
			</button>

			<button
				v-if="canPrev"
				type="button"
				class="absolute left-4 top-1/2 -translate-y-1/2 p-3 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors"
				title="Previous"
				@click.stop="emit('prev')"
			>
				<ChevronLeft :size="28" />
			</button>

			<button
				v-if="canNext"
				type="button"
				class="absolute right-4 top-1/2 -translate-y-1/2 p-3 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors"
				title="Next"
				@click.stop="emit('next')"
			>
				<ChevronRight :size="28" />
			</button>
		</div>
	</Teleport>
</template>
