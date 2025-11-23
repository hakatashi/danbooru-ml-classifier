import { initializeApp } from 'firebase/app'
import { getAuth } from 'firebase/auth'
import { getFirestore } from 'firebase/firestore'

const firebaseConfig = {
  apiKey: "AIzaSyC6wPDsfldDy3eG7bhfTvmUS5NYvDrbNTc",
  authDomain: "danbooru-ml-classifier.firebaseapp.com",
  projectId: "danbooru-ml-classifier",
  storageBucket: "danbooru-ml-classifier.appspot.com",
  messagingSenderId: "1043477842125",
  appId: "1:1043477842125:web:532804dba86f665134c184"
}

export const app = initializeApp(firebaseConfig)
export const auth = getAuth(app)
export const db = getFirestore(app)
