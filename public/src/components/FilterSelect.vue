<script setup lang="ts">
import {computed} from 'vue';

interface Props {
	label: string;
	provider: 'joycaption' | 'minicpm';
	min: number | null;
	max: number | null;
	minOptions: number[];
	maxOptions: number[];
}

const props = defineProps<Props>();

const emit = defineEmits<{
	'update:provider': ['joycaption' | 'minicpm'];
	'update:min': [number | null];
	'update:max': [number | null];
}>();

const localProvider = computed({
	get: () => props.provider,
	set: (value) => emit('update:provider', value),
});

const localMin = computed({
	get: () => props.min,
	set: (value) => emit('update:min', value),
});

const localMax = computed({
	get: () => props.max,
	set: (value) => emit('update:max', value),
});
</script>

<template>
	<div class="flex items-center gap-2 min-w-0">
		<label class="text-sm font-medium text-gray-700 shrink-0">
			{{ label }}:
		</label>
		<div class="flex items-center gap-2">
			<select
				v-model="localProvider"
				class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
			>
				<option value="minicpm">MiniCPM</option>
				<option value="joycaption">JoyCaption</option>
			</select>
			<select
				v-model.number="localMin"
				class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
			>
				<option :value="null">Min</option>
				<option v-for="val in minOptions" :key="val" :value="val">
					{{ val }}
				</option>
			</select>
			<span class="text-gray-500 text-sm">-</span>
			<select
				v-model.number="localMax"
				class="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
			>
				<option :value="null">Max</option>
				<option v-for="val in maxOptions" :key="val" :value="val">
					{{ val }}
				</option>
			</select>
		</div>
	</div>
</template>
