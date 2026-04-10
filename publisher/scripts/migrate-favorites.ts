/**
 * Migration script: copy favorites from images.favorites field
 * to the dedicated `favorites` Firestore collection.
 *
 * Usage:
 *   GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json \
 *   npx ts-node --project publisher/tsconfig.json publisher/scripts/migrate-favorites.ts
 *
 * Optional env vars:
 *   DRY_RUN=1   - Print what would be written without making changes
 */

import * as admin from 'firebase-admin';
import {getFirestore, FieldValue} from 'firebase-admin/firestore';

const DRY_RUN = process.env.DRY_RUN === '1';
const BATCH_SIZE = 500;

const main = async (): Promise<void> => {
	if (!process.env.GOOGLE_APPLICATION_CREDENTIALS) {
		console.error('GOOGLE_APPLICATION_CREDENTIALS is not set');
		process.exit(1);
	}

	const projectId = process.env.FIREBASE_PROJECT_ID ?? 'danbooru-ml-classifier';
	admin.initializeApp({
		credential: admin.credential.applicationDefault(),
		projectId,
	});
	const firestore = getFirestore();

	const imagesRef = firestore.collection('images');
	const favoritesRef = firestore.collection('favorites');

	console.log('Querying images with favorites.isFavorited == true...');
	const snapshot = await imagesRef
		.where('favorites.isFavorited', '==', true)
		.get();

	console.log(`Found ${snapshot.docs.length} favorited image(s).`);

	if (DRY_RUN) {
		for (const doc of snapshot.docs) {
			const {favorites} = doc.data();
			console.log(`  [DRY RUN] Would migrate: ${doc.id} → categories: ${JSON.stringify(favorites?.categories)}`);
		}
		console.log('Dry run complete. No changes written.');
		return;
	}

	let migrated = 0;

	// Process in batches of 500 (Firestore batch limit)
	for (let i = 0; i < snapshot.docs.length; i += BATCH_SIZE) {
		const chunk = snapshot.docs.slice(i, i + BATCH_SIZE);
		const batch = firestore.batch();

		for (const imageDoc of chunk) {
			const data = imageDoc.data();
			const favorites = data.favorites as {isFavorited: boolean; categories: string[]} | undefined;
			if (!favorites?.isFavorited) continue;

			const favDoc = favoritesRef.doc(imageDoc.id);
			batch.set(favDoc, {
				isFavorited: true,
				categories: favorites.categories ?? [],
				migratedAt: FieldValue.serverTimestamp(),
			});
		}

		await batch.commit();
		migrated += chunk.length;
		console.log(`Migrated ${Math.min(migrated, snapshot.docs.length)} / ${snapshot.docs.length}`);
	}

	console.log(`\nDone. Migrated ${migrated} favorite(s) to the 'favorites' collection.`);
	console.log('Note: the original favorites.isFavorited field in images/ has NOT been cleared.');
	console.log('The app now reads exclusively from the favorites/ collection.');
};

main().catch((error) => {
	console.error('Fatal error:', error);
	process.exit(1);
});
