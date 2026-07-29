<script setup lang="ts">
import {Tag} from 'lucide-vue-next';
import {onMounted, onUnmounted, ref, watch} from 'vue';
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
const rootEl = ref<HTMLElement | null>(null);

watch(isOpen, (opened) => {
	if (opened) {
		selected.value = new Set(props.initialSelected);
		newCategoryName.value = '';
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

function handleClickOutside(event: MouseEvent) {
	if (rootEl.value && !rootEl.value.contains(event.target as Node)) {
		isOpen.value = false;
	}
}

onMounted(() => document.addEventListener('mousedown', handleClickOutside));
onUnmounted(() =>
	document.removeEventListener('mousedown', handleClickOutside),
);
</script>

<template>
	<div ref="rootEl" class="relative inline-block">
		<button
			type="button"
			class="inline-flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium bg-white/90 text-gray-700 hover:bg-white shadow"
			:title="triggerLabel || 'Edit categories'"
			@click.stop.prevent="isOpen = !isOpen"
		>
			<Tag :size="13" />
			<span v-if="triggerLabel">{{ triggerLabel }}</span>
		</button>

		<div
			v-if="isOpen"
			:class="[
				'absolute z-50 mt-1 w-64 bg-white rounded-lg shadow-xl border border-gray-200 p-3',
				align === 'right' ? 'right-0' : 'left-0',
			]"
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
	</div>
</template>
