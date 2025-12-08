<script setup lang="ts">
import {computed} from 'vue';

interface Props {
	label: string;
	modelValue: string | null;
	placeholder?: string;
}

const props = withDefaults(defineProps<Props>(), {
	placeholder: '',
});

const emit = defineEmits<{
	'update:modelValue': [string | null];
}>();

const localValue = computed({
	get: () => props.modelValue,
	set: (value) => emit('update:modelValue', value || null),
});

function clear() {
	emit('update:modelValue', null);
}
</script>

<template>
	<div class="flex items-center gap-2 min-w-0">
		<label class="text-sm font-medium text-gray-700 shrink-0">
			{{ label }}:
		</label>
		<input
			v-model="localValue"
			type="text"
			:placeholder="placeholder"
			class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent w-32"
		>
		<button
			v-if="modelValue"
			@click="clear"
			class="p-1 text-gray-500 hover:text-gray-700"
			title="Clear filter"
		>
			<svg
				class="w-4 h-4"
				fill="none"
				stroke="currentColor"
				viewBox="0 0 24 24"
			>
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="2"
					d="M6 18L18 6M6 6l12 12"
				/>
			</svg>
		</button>
	</div>
</template>
