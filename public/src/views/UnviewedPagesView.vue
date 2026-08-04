<script setup lang="ts">
import type {User} from 'firebase/auth';
import {Check, Lock, RefreshCw} from 'lucide-vue-next';
import {computed, ref, watch} from 'vue';
import {RouterLink} from 'vue-router';
import {fetchDailyCounts} from '../api/mlApi';
import {usePageViews} from '../composables/usePageViews';
import {
	TRACKED_DAYS,
	TRACKED_PAGES,
	TRACKED_SORT_FIELD,
} from '../config/namedSorts';

const props = defineProps<{user: User | null}>();

const {isPageViewed, loadPageViews} = usePageViews();

const PAGE_SIZE = 50;

const loading = ref(false);
const error = ref<string | null>(null);
const dailyCounts = ref<Record<string, number>>({});

function todayString(): string {
	return new Date().toISOString().split('T')[0];
}

function offsetDateString(base: string, offsetDays: number): string {
	const [y, m, d] = base.split('-').map(Number);
	const date = new Date(y, m - 1, d - offsetDays);
	return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

// Most recent day first.
const trackedDates = computed(() => {
	const today = todayString();
	return Array.from({length: TRACKED_DAYS}, (_, i) =>
		offsetDateString(today, i),
	);
});

const oldestDate = computed(
	() => trackedDates.value[trackedDates.value.length - 1],
);

async function loadData() {
	loading.value = true;
	error.value = null;
	try {
		// Fetch daily counts for every month touched by the tracked window.
		const months = new Set(trackedDates.value.map((d) => d.slice(0, 7)));
		const results = await Promise.all(
			[...months].map((month) => fetchDailyCounts(month)),
		);
		const counts: Record<string, number> = {};
		for (const result of results) Object.assign(counts, result.days);
		dailyCounts.value = counts;

		await loadPageViews({
			sortField: TRACKED_SORT_FIELD,
			dateFrom: oldestDate.value,
			dateTo: trackedDates.value[0],
		});
	} catch (e) {
		error.value = (e as Error).message;
	} finally {
		loading.value = false;
	}
}

function pagesForDate(date: string): number[] {
	const count = dailyCounts.value[date] ?? 0;
	const existingPages = Math.min(TRACKED_PAGES, Math.ceil(count / PAGE_SIZE));
	return Array.from({length: existingPages}, (_, i) => i);
}

function viewed(date: string, page: number): boolean {
	return isPageViewed({date, sortField: TRACKED_SORT_FIELD, page});
}

function dailyLink(date: string, page: number): string {
	return `/daily?date=${date}&sort=${encodeURIComponent(TRACKED_SORT_FIELD)}&page=${page}`;
}

watch(
	() => props.user,
	(u) => {
		if (u) loadData();
	},
	{immediate: true},
);
</script>

<template>
	<div>
		<!-- Auth Required -->
		<div
			v-if="!user"
			class="bg-white rounded-2xl shadow-lg p-12 text-center max-w-md mx-auto"
		>
			<div
				class="w-20 h-20 bg-blue-100 rounded-full flex items-center justify-center mx-auto mb-6"
			>
				<Lock :size="40" class="text-blue-500" />
			</div>
			<h2 class="text-2xl font-bold text-gray-900 mb-3">
				Authentication Required
			</h2>
			<p class="text-gray-600">
				Please login with your Google account to view unviewed pages.
			</p>
		</div>

		<template v-else>
			<div class="bg-white rounded-xl shadow-md p-6 mb-4">
				<div class="flex items-center justify-between mb-1">
					<h1 class="text-xl font-bold text-gray-900">Unviewed Pages</h1>
					<button
						type="button"
						:disabled="loading"
						class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50"
						@click="loadData"
					>
						<RefreshCw :size="14" :class="loading && 'animate-spin'" />
						Refresh
					</button>
				</div>
				<p class="text-sm text-gray-500">
					Top {{ TRACKED_PAGES }} pages of the last {{ TRACKED_DAYS }} days,
					sorted by
					<span class="font-mono text-blue-600">{{ TRACKED_SORT_FIELD }}</span>.
					Click a cell to open that page; click "Mark page as viewed" there to
					check it off.
				</p>
			</div>

			<div
				v-if="error"
				class="bg-red-50 border border-red-200 rounded-xl p-6 text-center mb-4"
			>
				<p class="text-red-700">Error: {{ error }}</p>
			</div>

			<div class="bg-white rounded-xl shadow-md overflow-x-auto">
				<table class="w-full text-sm">
					<thead>
						<tr class="border-b border-gray-200">
							<th class="text-left px-4 py-2 font-medium text-gray-600">
								Date
							</th>
							<th
								v-for="p in TRACKED_PAGES"
								:key="p"
								class="text-center px-4 py-2 font-medium text-gray-600"
							>
								Page {{ p - 1 }}
							</th>
						</tr>
					</thead>
					<tbody>
						<tr
							v-for="date in trackedDates"
							:key="date"
							class="border-b border-gray-100 last:border-0"
						>
							<td class="px-4 py-2 text-gray-700 whitespace-nowrap">
								{{ date }}
							</td>
							<td
								v-for="p in TRACKED_PAGES"
								:key="p"
								class="text-center px-4 py-2"
							>
								<span
									v-if="!pagesForDate(date).includes(p - 1)"
									class="text-gray-300"
									>·</span
								>
								<RouterLink
									v-else
									:to="dailyLink(date, p - 1)"
									:class="[
										'inline-flex items-center justify-center w-7 h-7 rounded-md transition-colors',
										viewed(date, p - 1)
											? 'bg-green-100 text-green-700 hover:bg-green-200'
											: 'bg-red-50 text-red-500 hover:bg-red-100 border border-red-200',
									]"
									:title="viewed(date, p - 1) ? 'Viewed' : 'Not yet viewed'"
								>
									<Check v-if="viewed(date, p - 1)" :size="14" />
									<span v-else class="text-xs">?</span>
								</RouterLink>
							</td>
						</tr>
					</tbody>
				</table>
			</div>
		</template>
	</div>
</template>
