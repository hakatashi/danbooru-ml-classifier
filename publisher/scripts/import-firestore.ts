/**
 * Firestore → MongoDB import script
 *
 * Usage:
 *   GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json \
 *   MONGODB_URI=mongodb://localhost:27017 \
 *   npx ts-node --project tsconfig.json scripts/import-firestore.ts
 *
 * Optional env vars:
 *   MONGODB_DB       - MongoDB database name (default: danbooru-ml-classifier)
 *   IMPORT_BATCH     - Firestore page size per batch (default: 500)
 *   COLLECTIONS      - Comma-separated list of collections to import
 *                      (default: images,pixivRanking,danbooruRanking,gelbooruImage)
 */

import * as admin from 'firebase-admin';
import {getFirestore, Timestamp} from 'firebase-admin/firestore';
import {MongoClient} from 'mongodb';
import type {Db, Document} from 'mongodb';

type FirestoreDoc = Document & {_id: string};

// --- Config ---
const MONGODB_URI = process.env.MONGODB_URI ?? 'mongodb://localhost:27017';
const MONGODB_DB = process.env.MONGODB_DB ?? 'danbooru-ml-classifier';
const BATCH_SIZE = parseInt(process.env.IMPORT_BATCH ?? '500');
const COLLECTIONS_ENV = process.env.COLLECTIONS;
const DEFAULT_COLLECTIONS = ['images', 'pixivRanking', 'danbooruRanking', 'gelbooruImage'];
const COLLECTIONS = COLLECTIONS_ENV ? COLLECTIONS_ENV.split(',').map((s) => s.trim()) : DEFAULT_COLLECTIONS;

// --- Helpers ---

/**
 * Recursively convert Firestore Timestamps to JS Date objects.
 */
// eslint-disable-next-line valid-jsdoc
const convertTimestamps = (value: unknown): unknown => {
	if (value instanceof Timestamp) {
		return value.toDate();
	}
	if (Array.isArray(value)) {
		return value.map(convertTimestamps);
	}
	if (value !== null && typeof value === 'object') {
		const result: Record<string, unknown> = {};
		for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
			result[k] = convertTimestamps(v);
		}
		return result;
	}
	return value;
};

/**
 * Import a single Firestore collection into MongoDB.
 * Firestore document IDs become MongoDB `_id` fields.
 * Uses cursor-based pagination to handle arbitrarily large collections.
 */
// eslint-disable-next-line valid-jsdoc
const importCollection = async (
	firestore: admin.firestore.Firestore,
	mongo: Db,
	collectionName: string,
): Promise<void> => {
	console.log(`\n[${collectionName}] Starting import...`);

	const mongoCollection = mongo.collection<FirestoreDoc>(collectionName);
	const query = firestore.collection(collectionName).orderBy('__name__').limit(BATCH_SIZE);
	let totalImported = 0;
	let lastDoc: admin.firestore.QueryDocumentSnapshot | null = null;

	while (true) {
		const paginatedQuery: admin.firestore.Query<admin.firestore.DocumentData> =
			lastDoc === null ? query : query.startAfter(lastDoc);
		const snapshot: admin.firestore.QuerySnapshot<admin.firestore.DocumentData> =
			await paginatedQuery.get();

		if (snapshot.empty) {
			break;
		}

		const operations = snapshot.docs.map((doc: admin.firestore.QueryDocumentSnapshot<admin.firestore.DocumentData>) => {
			const data = convertTimestamps(doc.data()) as Record<string, unknown>;
			return {
				updateOne: {
					filter: {_id: doc.id},
					update: {$set: {_id: doc.id, ...data}},
					upsert: true,
				},
			};
		});

		await mongoCollection.bulkWrite(operations);
		totalImported += snapshot.docs.length;
		lastDoc = snapshot.docs[snapshot.docs.length - 1];

		console.log(`[${collectionName}] Imported ${totalImported} documents so far...`);

		if (snapshot.docs.length < BATCH_SIZE) {
			break;
		}
	}

	console.log(`[${collectionName}] Done. Total: ${totalImported} documents`);
};

// --- Main ---

const main = async (): Promise<void> => {
	if (!process.env.GOOGLE_APPLICATION_CREDENTIALS) {
		console.error('GOOGLE_APPLICATION_CREDENTIALS is not set');
		// eslint-disable-next-line no-process-exit, node/no-process-exit
		process.exit(1);
	}

	console.log(`Importing collections: ${COLLECTIONS.join(', ')}`);
	console.log(`Firestore credentials: ${process.env.GOOGLE_APPLICATION_CREDENTIALS}`);
	console.log(`MongoDB URI: ${MONGODB_URI}`);
	console.log(`MongoDB DB: ${MONGODB_DB}`);
	console.log(`Batch size: ${BATCH_SIZE}`);

	admin.initializeApp({credential: admin.credential.applicationDefault()});
	const firestore = getFirestore();

	const mongoClient = new MongoClient(MONGODB_URI);
	await mongoClient.connect();
	const mongo = mongoClient.db(MONGODB_DB);

	console.log('\nConnected to Firestore and MongoDB.');

	try {
		for (const collectionName of COLLECTIONS) {
			await importCollection(firestore, mongo, collectionName);
		}
		console.log('\nAll collections imported successfully.');
	} finally {
		await mongoClient.close();
	}
};

main().catch((error) => {
	console.error('Fatal error:', error);
	// eslint-disable-next-line no-process-exit, node/no-process-exit
	process.exit(1);
});
