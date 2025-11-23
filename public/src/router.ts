import { createRouter, createWebHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'
import ImageDetailView from './views/ImageDetailView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomeView
    },
    {
      path: '/image/:id',
      name: 'image-detail',
      component: ImageDetailView
    }
  ]
})

export default router
