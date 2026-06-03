/**
 * Firestore コレクション削除スクリプト
 *
 * MongoDB移行済みで、公開サイト・Firebase Functionsから
 * 参照されなくなったFirestoreコレクションを削除します。
 *
 * 削除対象:
 *   - pixivRanking      (MongoDB移行済み)
 *   - danbooruRanking   (MongoDB移行済み)
 *   - gelbooruImage     (MongoDB移行済み)
 *   - sankakuImage      (MongoDB移行済み)
 *   - pixivPages        (MongoDB移行済み)
 *
 * Usage:
 *   GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json \
 *   npx ts-node --project tsconfig.json scripts/delete-firestore-collections.ts
 *
 *   # ドライランで削除対象のドキュメント数を確認:
 *   DRY_RUN=true ... npx ts-node ...
 *
 *   # 対象コレクションを絞る:
 *   COLLECTIONS=pixivRanking,danbooruRanking ... npx ts-node ...
 */

import * as admin from 'firebase-admin';
import {getFirestore} from 'firebase-admin/firestore';

const BATCH_SIZE = 500;
const DRY_RUN = process.env.DRY_RUN === 'true';
const COLLECTIONS_ENV = process.env.COLLECTIONS;
const DEFAULT_COLLECTIONS = [
	'pixivRanking',
	'danbooruRanking',
	'gelbooruImage',
	'sankakuImage',
	'pixivPages',
];
const COLLECTIONS = COLLECTIONS_ENV
	? COLLECTIONS_ENV.split(',').map((s) => s.trim())
	: DEFAULT_COLLECTIONS;

const deleteCollection = async (
	firestore: admin.firestore.Firestore,
	collectionName: string,
): Promise<void> => {
	console.log(`\n[${collectionName}] Starting deletion...`);

	const collRef = firestore.collection(collectionName);
	let totalDeleted = 0;

	while (true) {
		const snapshot = await collRef.limit(BATCH_SIZE).get();

		if (snapshot.empty) {
			break;
		}

		if (DRY_RUN) {
			totalDeleted += snapshot.docs.length;
			console.log(
				`[${collectionName}] (dry-run) Found ${totalDeleted} documents so far...`,
			);
			if (snapshot.docs.length < BATCH_SIZE) {
				break;
			}
			// dry-run では startAfter で次ページへ進む
			const last = snapshot.docs[snapshot.docs.length - 1];
			const next = await collRef.startAfter(last).limit(BATCH_SIZE).get();
			if (next.empty) break;
			totalDeleted += next.docs.length;
			let cursor = next.docs[next.docs.length - 1];
			while (true) {
				const page = await collRef.startAfter(cursor).limit(BATCH_SIZE).get();
				if (page.empty) break;
				totalDeleted += page.docs.length;
				if (page.docs.length < BATCH_SIZE) break;
				cursor = page.docs[page.docs.length - 1];
			}
			break;
		}

		const batch = firestore.batch();
		snapshot.docs.forEach((doc) => batch.delete(doc.ref));
		await batch.commit();

		totalDeleted += snapshot.docs.length;
		console.log(`[${collectionName}] Deleted ${totalDeleted} documents so far...`);

		if (snapshot.docs.length < BATCH_SIZE) {
			break;
		}
	}

	if (DRY_RUN) {
		console.log(
			`[${collectionName}] (dry-run) Would delete ${totalDeleted} documents`,
		);
	} else {
		console.log(
			`[${collectionName}] Done. Total deleted: ${totalDeleted} documents`,
		);
	}
};

const main = async (): Promise<void> => {
	if (!process.env.GOOGLE_APPLICATION_CREDENTIALS) {
		console.error('GOOGLE_APPLICATION_CREDENTIALS is not set');
		// eslint-disable-next-line no-process-exit, node/no-process-exit
		process.exit(1);
	}

	console.log(DRY_RUN ? '=== DRY RUN MODE ===' : '=== DELETE MODE ===');
	console.log(`Target collections: ${COLLECTIONS.join(', ')}`);
	console.log(
		`Firestore credentials: ${process.env.GOOGLE_APPLICATION_CREDENTIALS}`,
	);

	admin.initializeApp({credential: admin.credential.applicationDefault()});
	const firestore = getFirestore();

	for (const collectionName of COLLECTIONS) {
		await deleteCollection(firestore, collectionName);
	}

	console.log('\nAll done.');
};

main().catch((error) => {
	console.error('Fatal error:', error);
	// eslint-disable-next-line no-process-exit, node/no-process-exit
	process.exit(1);
});
