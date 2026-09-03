<script setup lang="ts">
import {ExternalLink} from 'lucide-vue-next';
import type {TwitterSourceData} from '../types';

defineProps<{source: TwitterSourceData}>();
</script>

<template>
	<div class="bg-white rounded-xl shadow-md p-4">
		<h2 class="text-base font-semibold text-gray-900 mb-3">Twitter Source</h2>
		<div class="space-y-2 text-sm">
			<div v-if="source.user" class="flex flex-wrap items-center gap-2">
				<a
					v-if="source.user.screen_name"
					:href="`https://twitter.com/${source.user.screen_name}`"
					target="_blank"
					rel="noopener noreferrer"
					class="text-blue-600 hover:underline font-medium flex items-center gap-1"
				>
					@{{ source.user.screen_name }}
					<ExternalLink :size="12" />
				</a>
				<span v-if="source.user.name" class="text-gray-600"
					>{{ source.user.name }}</span
				>
			</div>
			<div
				v-if="source.text"
				class="text-gray-700 bg-gray-50 rounded-lg p-2 whitespace-pre-wrap break-words text-xs"
			>
				{{ source.text }}
			</div>
			<a
				v-if="source.tweetId"
				:href="`https://twitter.com/i/web/status/${source.tweetId}`"
				target="_blank"
				rel="noopener noreferrer"
				class="text-blue-600 hover:underline text-xs flex items-center gap-1"
			>
				View Tweet <ExternalLink :size="11" />
			</a>
		</div>
	</div>
</template>
