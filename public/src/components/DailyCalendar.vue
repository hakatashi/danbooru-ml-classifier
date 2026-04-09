<script setup lang="ts">
import {ChevronLeft, ChevronRight} from 'lucide-vue-next';
import {computed, ref, watch} from 'vue';

const props = defineProps<{
	modelValue: string; // YYYY-MM-DD
	counts: Record<string, number>; // { "YYYY-MM-DD": count }
}>();

const emit = defineEmits<{
	(e: 'update:modelValue', value: string): void;
	(e: 'month-change', month: string): void;
}>();

// Current display month (derived from modelValue initially)
const displayYear = ref(
	props.modelValue
		? Number.parseInt(props.modelValue.split('-')[0], 10)
		: new Date().getFullYear(),
);
const displayMonth = ref(
	props.modelValue
		? Number.parseInt(props.modelValue.split('-')[1], 10) - 1
		: new Date().getMonth(),
);

watch(
	() => props.modelValue,
	(v) => {
		if (!v) return;
		const [y, m] = v.split('-').map(Number);
		displayYear.value = y;
		displayMonth.value = m - 1;
	},
);

const monthLabel = computed(() => {
	return new Date(displayYear.value, displayMonth.value, 1).toLocaleString(
		'en-US',
		{year: 'numeric', month: 'long'},
	);
});

const weekdayLabels = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

const firstDayOffset = computed(() => {
	return new Date(displayYear.value, displayMonth.value, 1).getDay();
});

const daysInMonth = computed(() => {
	return new Date(displayYear.value, displayMonth.value + 1, 0).getDate();
});

function formatDate(day: number): string {
	const m = String(displayMonth.value + 1).padStart(2, '0');
	const d = String(day).padStart(2, '0');
	return `${displayYear.value}-${m}-${d}`;
}

// Max count for this displayed month (for relative color scaling)
// Reference: max of (max visible count, 10000)
const maxCountForMonth = computed(() => {
	let max = 0;
	for (let d = 1; d <= daysInMonth.value; d++) {
		const count = props.counts[formatDate(d)] ?? 0;
		if (count > max) max = count;
	}
	return Math.max(max, 10000);
});

function getCountStyle(day: number): {
	backgroundColor?: string;
	color?: string;
} {
	const date = formatDate(day);
	const count = props.counts[date] ?? 0;
	if (count === 0) return {};
	// Intensity 0..1 relative to reference max
	const intensity = Math.min(count / maxCountForMonth.value, 1);
	// Map intensity to blue shades: low → rgb(219,234,254) [blue-100], high → rgb(37,99,235) [blue-600]
	const r = Math.round(219 - (219 - 37) * intensity);
	const g = Math.round(234 - (234 - 99) * intensity);
	const b = Math.round(254 - (254 - 235) * intensity);
	const textColor = intensity > 0.5 ? '#fff' : '#1e3a5f';
	return {
		backgroundColor: `rgb(${r},${g},${b})`,
		color: textColor,
	};
}

function isSelected(day: number): boolean {
	return formatDate(day) === props.modelValue;
}

function selectDay(day: number) {
	emit('update:modelValue', formatDate(day));
}

function prevMonth() {
	if (displayMonth.value === 0) {
		displayMonth.value = 11;
		displayYear.value--;
	} else {
		displayMonth.value--;
	}
	emit('month-change', currentYearMonth.value);
}

function nextMonth() {
	if (displayMonth.value === 11) {
		displayMonth.value = 0;
		displayYear.value++;
	} else {
		displayMonth.value++;
	}
	emit('month-change', currentYearMonth.value);
}

const currentYearMonth = computed(() => {
	const m = String(displayMonth.value + 1).padStart(2, '0');
	return `${displayYear.value}-${m}`;
});

defineExpose({currentYearMonth});
</script>

<template>
	<div
		class="bg-white rounded-xl shadow-lg border border-gray-200 p-3 w-72 select-none"
	>
		<!-- Header -->
		<div class="flex items-center justify-between mb-3">
			<button
				type="button"
				@click="prevMonth"
				class="p-1.5 rounded-lg hover:bg-gray-100 transition-colors text-gray-600"
			>
				<ChevronLeft :size="16" />
			</button>
			<span class="text-sm font-semibold text-gray-800">{{ monthLabel }}</span>
			<button
				type="button"
				@click="nextMonth"
				class="p-1.5 rounded-lg hover:bg-gray-100 transition-colors text-gray-600"
			>
				<ChevronRight :size="16" />
			</button>
		</div>

		<!-- Weekday labels -->
		<div class="grid grid-cols-7 mb-1">
			<div
				v-for="label in weekdayLabels"
				:key="label"
				class="text-center text-xs text-gray-400 font-medium py-1"
			>
				{{ label }}
			</div>
		</div>

		<!-- Day grid -->
		<div class="grid grid-cols-7 gap-0.5">
			<!-- Empty cells for first week offset -->
			<div v-for="i in firstDayOffset" :key="`empty-${i}`" />

			<!-- Day cells -->
			<button
				v-for="day in daysInMonth"
				:key="day"
				type="button"
				@click="selectDay(day)"
				:class="[
					'aspect-square flex items-center justify-center text-xs rounded-md transition-colors font-medium',
					isSelected(day)
						? 'ring-2 ring-blue-600 ring-offset-1'
						: (counts[formatDate(day)] ?? 0) === 0
							? 'bg-gray-50 text-gray-400 hover:bg-gray-100'
							: '',
				]"
				:style="!isSelected(day) && (counts[formatDate(day)] ?? 0) > 0 ? getCountStyle(day) : undefined"
				:title="`${formatDate(day)}: ${counts[formatDate(day)] ?? 0} images`"
			>
				{{ day }}
			</button>
		</div>

		<!-- Legend -->
		<div
			class="mt-3 pt-2 border-t border-gray-100 flex items-center gap-2 text-xs text-gray-500 flex-wrap"
		>
			<span>Images:</span>
			<div class="flex items-center gap-1">
				<div class="w-3 h-3 rounded bg-gray-100 border border-gray-200" />
				<span>0</span>
			</div>
			<div class="flex items-center gap-1">
				<div class="w-3 h-3 rounded" style="background:rgb(189,219,254)" />
				<span>few</span>
			</div>
			<div class="flex items-center gap-1">
				<div class="w-3 h-3 rounded" style="background:rgb(96,165,250)" />
				<span>many</span>
			</div>
			<div class="flex items-center gap-1">
				<div class="w-3 h-3 rounded" style="background:rgb(37,99,235)" />
				<span>max</span>
			</div>
		</div>
	</div>
</template>
