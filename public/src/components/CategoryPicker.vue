<script setup lang="ts">
import {Tag} from 'lucide-vue-next';
import {nextTick, onMounted, onUnmounted, ref, watch} from 'vue';
import type {FavoriteCategoryCount} from '../api/mlApi';

const props = withDefaults(
	defineProps<{
		categories: FavoriteCategoryCount[];
		initialSelected?: string[];
		triggerLabel?: string;
		applyLabel?: string;
		align?: 'left' | 'right';
	}>(),
	{
		initialSelected: () => [],
		triggerLabel: '',
		applyLabel: 'Apply',
		align: 'left',
	},
);

const emit = defineEmits<{
	apply: [categories: string[]];
}>();

const isOpen = ref(false);
const selected = ref<Set<string>>(new Set(props.initialSelected));
const newCategoryName = ref('');
const triggerEl = ref<HTMLElement | null>(null);
const panelEl = ref<HTMLElement | null>(null);
const panelStyle = ref<{top: string; left: string}>({top: '0px', left: '0px'});

const PANEL_WIDTH = 256; // matches w-64

function updatePosition() {
	if (!triggerEl.value) return;
	const rect = triggerEl.value.getBoundingClientRect();
	let left = props.align === 'right' ? rect.right - PANEL_WIDTH : rect.left;
	// Clamp so the panel never overflows the viewport edges.
	left = Math.min(Math.max(left, 8), window.innerWidth - PANEL_WIDTH - 8);
	panelStyle.value = {
		top: `${rect.bottom + 4}px`,
		left: `${left}px`,
	};
}

watch(isOpen, (opened) => {
	if (opened) {
		selected.value = new Set(props.initialSelected);
		newCategoryName.value = '';
		nextTick(updatePosition);
	}
});

function toggle(name: string) {
	if (selected.value.has(name)) {
		selected.value.delete(name);
	} else {
		selected.value.add(name);
	}
	// Trigger reactivity for the Set
	selected.value = new Set(selected.value);
}

function addNewCategory() {
	const name = newCategoryName.value.trim();
	if (!name || name.length > 100) return;
	selected.value = new Set(selected.value).add(name);
	newCategoryName.value = '';
}

function applyAndClose() {
	emit('apply', [...selected.value]);
	isOpen.value = false;
}

function handleOutsideEvent(event: Event) {
	const target = event.target as Node;
	if (
		triggerEl.value &&
		!triggerEl.value.contains(target) &&
		panelEl.value &&
		!panelEl.value.contains(target)
	) {
		isOpen.value = false;
	}
}

function handleWindowChange() {
	if (isOpen.value) updatePosition();
}

// Since the panel is teleported to <body>, it escapes any ancestor's
// overflow:hidden/z-index stacking context -- image tiles in
// JustifiedGallery clip absolutely-positioned overlays to their own bounds,
// which previously made this popover invisible behind neighboring tiles.
onMounted(() => {
	document.addEventListener('mousedown', handleOutsideEvent);
	window.addEventListener('scroll', handleWindowChange, true);
	window.addEventListener('resize', handleWindowChange);
});

onUnmounted(() => {
	document.removeEventListener('mousedown', handleOutsideEvent);
	window.removeEventListener('scroll', handleWindowChange, true);
	window.removeEventListener('resize', handleWindowChange);
});
</script>

<template>
	<span ref="triggerEl" class="inline-block">
		<button
			type="button"
			class="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium bg-white/90 text-gray-700 hover:bg-white shadow"
			:title="triggerLabel || 'Edit categories'"
			@click.stop.prevent="isOpen = !isOpen"
		>
			<Tag :size="13" />
			<span v-if="triggerLabel">{{ triggerLabel }}</span>
		</button>

		<Teleport to="body">
			<div
				v-if="isOpen"
				ref="panelEl"
				class="fixed z-[60] w-64 bg-white rounded-lg shadow-xl border border-gray-200 p-3"
				:style="panelStyle"
				@click.stop
			>
				<div class="max-h-48 overflow-y-auto space-y-1 mb-2">
					<label
						v-for="cat in categories"
						:key="cat.name"
						class="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-gray-50 cursor-pointer text-sm"
					>
						<input
							type="checkbox"
							:checked="selected.has(cat.name)"
							class="rounded border-gray-300"
							@change="toggle(cat.name)"
						>
						<span class="flex-1 text-gray-700 truncate">{{ cat.name }}</span>
						<span class="text-xs text-gray-400">{{ cat.count }}</span>
					</label>
					<p
						v-if="categories.length === 0"
						class="text-xs text-gray-400 px-1.5 py-1"
					>
						No categories yet
					</p>
				</div>

				<div class="flex items-center gap-1.5 mb-3">
					<input
						v-model="newCategoryName"
						type="text"
						maxlength="100"
						placeholder="New category"
						class="flex-1 min-w-0 px-2 py-1 text-xs border border-gray-300 rounded"
						@keydown.enter.prevent="addNewCategory"
					>
					<button
						type="button"
						class="px-2 py-1 text-xs font-medium bg-gray-100 hover:bg-gray-200 rounded"
						@click="addNewCategory"
					>
						Add
					</button>
				</div>

				<div class="flex justify-end gap-2">
					<button
						type="button"
						class="px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-100 rounded"
						@click="isOpen = false"
					>
						Cancel
					</button>
					<button
						type="button"
						class="px-3 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded"
						@click="applyAndClose"
					>
						{{ applyLabel }}
					</button>
				</div>
			</div>
		</Teleport>
	</span>
</template>
