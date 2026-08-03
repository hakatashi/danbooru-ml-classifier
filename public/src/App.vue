<script setup lang="ts">
import type {User} from 'firebase/auth';
import {Menu, X} from 'lucide-vue-next';
import {ref} from 'vue';
import {RouterLink, RouterView} from 'vue-router';
import AuthButton from './components/AuthButton.vue';
import {useFavorites} from './composables/useFavorites';
import {useImages} from './composables/useImages';
import {usePageViews} from './composables/usePageViews';

const user = ref<User | null>(null);
const {clearCache} = useImages();
const {clearFavoritesCache} = useFavorites();
const {clearPageViewsCache} = usePageViews();
const mobileMenuOpen = ref(false);

const NAV_LINKS = [
	{to: '/daily', label: 'Daily Recommendation'},
	{to: '/daily/unviewed', label: 'Unviewed Pages'},
	{to: '/favorites', label: 'Favorites'},
	{to: '/gallery', label: 'Gallery'},
	{to: '/archives', label: 'Archives'},
	{to: '/novels', label: 'Novels'},
];

function onAuthChange(newUser: User | null) {
	user.value = newUser;
	if (!newUser) {
		clearCache();
		clearFavoritesCache();
		clearPageViewsCache();
	}
}
</script>

<template>
	<div class="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
		<!-- Header -->
		<header class="bg-white shadow-sm sticky top-0 z-40">
			<div class="w-full px-4 sm:px-6 lg:px-8 py-4">
				<div class="flex justify-between items-center gap-2">
					<div class="flex items-center gap-6 min-w-0">
						<RouterLink
							to="/"
							class="text-2xl font-bold text-gray-900 hover:text-blue-600 transition-colors flex-shrink-0"
						>
							DMC
						</RouterLink>
						<nav class="hidden lg:flex gap-4">
							<RouterLink
								v-for="link in NAV_LINKS"
								:key="link.to"
								:to="link.to"
								class="text-sm font-medium text-gray-600 hover:text-blue-600 transition-colors whitespace-nowrap"
								active-class="text-blue-600"
							>
								{{ link.label }}
							</RouterLink>
						</nav>
					</div>
					<div class="flex items-center gap-2 flex-shrink-0">
						<AuthButton @auth-change="onAuthChange" />
						<button
							type="button"
							class="lg:hidden p-2 rounded-lg hover:bg-gray-100 text-gray-600"
							title="Menu"
							@click="mobileMenuOpen = !mobileMenuOpen"
						>
							<X v-if="mobileMenuOpen" :size="22" />
							<Menu v-else :size="22" />
						</button>
					</div>
				</div>

				<!-- Mobile nav menu -->
				<nav
					v-if="mobileMenuOpen"
					class="lg:hidden flex flex-col mt-3 pt-3 border-t border-gray-100"
				>
					<RouterLink
						v-for="link in NAV_LINKS"
						:key="link.to"
						:to="link.to"
						class="px-2 py-2.5 rounded-lg text-sm font-medium text-gray-600 hover:bg-gray-50 hover:text-blue-600 transition-colors"
						active-class="text-blue-600 bg-blue-50"
						@click="mobileMenuOpen = false"
					>
						{{ link.label }}
					</RouterLink>
				</nav>
			</div>
		</header>

		<main class="w-full px-4 sm:px-6 lg:px-8 py-6">
			<RouterView :user="user" />
		</main>
	</div>
</template>
