<script setup lang="ts">
import {useScoreDisplay} from '../composables/useScoreDisplay';
import type {ModerationData} from '../types';

defineProps<{moderations: Record<string, ModerationData>}>();

const {getRatingColorClass, getRatingLabel} = useScoreDisplay();
</script>

<template>
	<div class="bg-white rounded-xl shadow-md p-4">
		<h2 class="text-base font-semibold text-gray-900 mb-3">
			Moderation Ratings
		</h2>
		<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
			<div
				v-for="(modData, model) in moderations"
				:key="model"
				class="border border-gray-100 rounded-lg p-3"
			>
				<div class="flex items-center justify-between mb-2">
					<span class="font-medium text-gray-700 capitalize text-sm"
						>{{ model }}</span
					>
					<span
						:class="[
							getRatingColorClass(modData.result),
							'px-2 py-0.5 rounded-full text-white text-xs font-semibold',
						]"
					>
						{{ modData.result ?? 'N/A' }}
						— {{ getRatingLabel(modData.result) }}
					</span>
				</div>
				<p
					v-if="modData.explanation"
					class="text-xs text-gray-600 leading-relaxed"
				>
					{{ modData.explanation }}
				</p>
			</div>
		</div>
	</div>
</template>
