import {createRouter, createWebHistory} from 'vue-router';
import HomeView from './views/HomeView.vue';
import ImageDetailView from './views/ImageDetailView.vue';
import NovelsListView from './views/NovelsListView.vue';
import NovelView from './views/NovelView.vue';

const router = createRouter({
	history: createWebHistory(),
	routes: [
		{
			path: '/',
			name: 'home',
			component: HomeView,
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
