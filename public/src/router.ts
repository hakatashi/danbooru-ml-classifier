import {createRouter, createWebHistory} from 'vue-router';
import ArchivesView from './views/ArchivesView.vue';
import DailyImageDetailView from './views/DailyImageDetailView.vue';
import DailyRecommendationView from './views/DailyRecommendationView.vue';
import FavoritesView from './views/FavoritesView.vue';
import GalleryView from './views/GalleryView.vue';
import ImageDetailView from './views/ImageDetailView.vue';
import NovelsListView from './views/NovelsListView.vue';
import NovelView from './views/NovelView.vue';
import UnviewedPagesView from './views/UnviewedPagesView.vue';

const router = createRouter({
	history: createWebHistory(),
	routes: [
		{
			path: '/',
			redirect: '/daily',
		},
		{
			path: '/daily',
			name: 'daily',
			component: DailyRecommendationView,
		},
		{
			path: '/daily/image/:id',
			name: 'daily-image-detail',
			component: DailyImageDetailView,
		},
		{
			path: '/daily/unviewed',
			name: 'daily-unviewed',
			component: UnviewedPagesView,
		},
		{
			path: '/favorites',
			name: 'favorites',
			component: FavoritesView,
		},
		{
			// Random-viewer gallery over favorited images. This path used to be
			// the grid/justified Archives view (route name 'home') -- that view
			// moved to /archives below, and this URL was repurposed rather than
			// redirected, per an explicit product decision.
			path: '/gallery',
			name: 'gallery',
			component: GalleryView,
		},
		{
			path: '/archives',
			name: 'archives',
			component: ArchivesView,
		},
		{
			path: '/image/:id(.*)',
			name: 'image-detail',
			component: ImageDetailView,
		},
		{
			path: '/novels',
			name: 'novels-list',
			component: NovelsListView,
		},
		{
			path: '/novels/:novelId',
			name: 'novel-view',
			component: NovelView,
		},
	],
});

export default router;
