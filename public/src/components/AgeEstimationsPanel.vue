<script setup lang="ts">
import type {AgeEstimationData} from '../types';

defineProps<{ageEstimations: Record<string, AgeEstimationData>}>();
</script>

<template>
	<div class="bg-white rounded-xl shadow-md p-4">
		<h2 class="text-base font-semibold text-gray-900 mb-3">Age Estimations</h2>
		<div class="space-y-4">
			<div
				v-for="(ageData, model) in ageEstimations"
				:key="model"
				class="border border-gray-100 rounded-lg p-3"
			>
				<div class="flex items-center justify-between mb-2">
					<span class="font-medium text-gray-700 capitalize text-sm"
						>{{ model }}</span
					>
					<span class="text-xs text-gray-500">
						{{ ageData.result.characters_detected }}
						character(s)
					</span>
				</div>
				<div
					v-if="ageData.result.characters.length > 0"
					class="grid grid-cols-1 sm:grid-cols-2 gap-2"
				>
					<div
						v-for="char in ageData.result.characters"
						:key="char.id"
						class="bg-gray-50 rounded-lg p-2"
					>
						<div class="flex items-center justify-between mb-1">
							<span class="text-xs font-medium text-gray-700">
								Character {{ char.id }} ({{ char.gender_guess }})
							</span>
							<span class="text-sm font-bold text-blue-600">
								{{ char.most_likely_age ?? '?' }}
							</span>
						</div>
						<p class="text-xs text-gray-500">{{ char.estimated_age_range }}</p>
						<div class="mt-1 w-full bg-gray-200 rounded-full h-1">
							<div
								class="h-1 rounded-full bg-blue-400"
								:style="{width: `${char.confidence * 100}%`}"
							/>
						</div>
					</div>
				</div>
			</div>
		</div>
	</div>
</template>
