import { ref } from 'vue'
import { collection, query, orderBy, limit, getDocs, doc, getDoc, startAfter, type QueryDocumentSnapshot, type DocumentData } from 'firebase/firestore'
import { db } from '../firebase'
import type { ImageDocument } from '../types'

export interface SortOption {
  field: string
  direction: 'asc' | 'desc'
}

const PAGE_SIZE = 100

const images = ref<ImageDocument[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const hasMore = ref(true)
const lastDoc = ref<QueryDocumentSnapshot<DocumentData> | null>(null)
const currentSort = ref<SortOption | null>(null)

export function useImages() {
  async function loadImages(sort: SortOption) {
    // If sort changed, reset
    if (currentSort.value?.field !== sort.field || currentSort.value?.direction !== sort.direction) {
      images.value = []
      lastDoc.value = null
      hasMore.value = true
      currentSort.value = sort
    }

    if (!hasMore.value) return

    loading.value = true
    error.value = null

    try {
      const imagesRef = collection(db, 'images')

      let q
      if (lastDoc.value) {
        q = query(
          imagesRef,
          orderBy(sort.field, sort.direction),
          startAfter(lastDoc.value),
          limit(PAGE_SIZE)
        )
      } else {
        q = query(
          imagesRef,
          orderBy(sort.field, sort.direction),
          limit(PAGE_SIZE)
        )
      }

      const snapshot = await getDocs(q)

      if (snapshot.empty) {
        hasMore.value = false
        return
      }

      const newImages: ImageDocument[] = []
      snapshot.forEach((doc) => {
        const data = doc.data()
        // Filter: only include images with captions
        if (data.captions && Object.keys(data.captions).length > 0) {
          newImages.push({
            id: doc.id,
            ...data
          } as ImageDocument)
        }
      })

      images.value = [...images.value, ...newImages]
      lastDoc.value = snapshot.docs[snapshot.docs.length - 1] ?? null

      if (snapshot.docs.length < PAGE_SIZE) {
        hasMore.value = false
      }
    } catch (e) {
      console.error('Error loading images:', e)
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  async function loadMore() {
    if (currentSort.value && hasMore.value && !loading.value) {
      await loadImages(currentSort.value)
    }
  }

  async function getImageById(id: string): Promise<ImageDocument | null> {
    // First check if already loaded
    const cached = images.value.find(img => img.id === id)
    if (cached) return cached

    // Fetch from Firestore
    try {
      const docRef = doc(db, 'images', id)
      const docSnap = await getDoc(docRef)
      if (docSnap.exists()) {
        return {
          id: docSnap.id,
          ...docSnap.data()
        } as ImageDocument
      }
      return null
    } catch (e) {
      console.error('Error fetching image:', e)
      return null
    }
  }

  function clearCache() {
    images.value = []
    lastDoc.value = null
    hasMore.value = true
    currentSort.value = null
  }

  return {
    images,
    loading,
    error,
    hasMore,
    loadImages,
    loadMore,
    getImageById,
    clearCache
  }
}
