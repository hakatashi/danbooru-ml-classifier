/**
 * One-shot migration: copy the Firestore `favorites` collection into the
 * `favorites` subdocument of the corresponding MongoDB `images` document.
 *
 * `images._id` is an ObjectId for images ingested by the local cron fetchers
 * and a plain string (the Firestore document ID) for the legacy Firestore
 * import (see import-firestore.ts). Firestore `favorites` document IDs use
 * the same ID space, so each ID is resolved as an ObjectId first and falls
 * back to a string `_id` match.
 *
 * `favoritedAt` has no dedicated source field in Firestore. It is derived,
 * in priority order, from `favoritedAt` (future-proofing; unused today),
 * `migratedAt` (written by the old migrate-favorites.ts for legacy Twitter
 * favorites), or the Firestore document's `createTime` as a last resort.
 *
 * SAFETY: unless --force is passed, the script aborts if MongoDB's newest
 * `favorites.updatedAt` is newer than Firestore's newest favorites
 * `updateTime` — this means the app has already cut over to writing
 * favorites directly to MongoDB, and re-running this script would
 * overwrite/destroy post-cutover data with stale Firestore state.
 *
 * Usage:
 *   GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json \
 *   npx ts-node --project tsconfig.json scripts/migrate-favorites-to-mongo.ts [--dry-run] [--prune] [--force]
 *
 * Optional env vars:
 *   MONGODB_URI - MongoDB URI (default: mongodb://localhost:27017)
 *   MONGODB_DB  - Database name (default: danbooru-ml-classifier)
 */

import 'dotenv/config';
import * as admin from 'firebase-admin';
import {getFirestore} from 'firebase-admin/firestore';
import {ObjectId} from 'mongodb';
import type {AnyBulkWriteOperation} from 'mongodb';
import {getDb, closeDb} from '../src/db';
import {ensureIndexes} from './ensure-indexes';

// images._id is an ObjectId for cron-ingested docs and a plain string for
// legacy Firestore imports (see import-firestore.ts), so the collection
// must be typed to allow both.
interface ImageDoc {
	_id: string | ObjectId;
	favorites?: {updatedAt?: Date};
}

const DRY_RUN = process.argv.includes('--dry-run');
const PRUNE = process.argv.includes('--prune');
const FORCE = process.argv.includes('--force');
const WRITE_CHUNK = 500;

const OBJECT_ID_RE = /^[0-9a-f]{24}$/;

interface FavoritesShape {
	isFavorited: boolean;
	categories: string[];
	favoritedAt: Date | null;
	updatedAt: Date;
}

interface ResolvedFavorite {
	firestoreId: string;
	mongoId: string | ObjectId;
	idKind: 'objectId' | 'string';
	favoritedAtSource: 'favoritedAt' | 'migratedAt' | 'createTime';
	favorites: FavoritesShape;
}

const main = async (): Promise<void> => {
	if (!process.env.GOOGLE_APPLICATION_CREDENTIALS) {
		console.error('GOOGLE_APPLICATION_CREDENTIALS is not set');
		process.exitCode = 1;
		return;
	}

	const projectId = process.env.FIREBASE_PROJECT_ID ?? 'danbooru-ml-classifier';
	admin.initializeApp({
		credential: admin.credential.applicationDefault(),
		projectId,
	});
	const firestore = getFirestore();
	const db = await getDb();
	const imagesCollection = db.collection<ImageDoc>('images');

	try {
		console.log('Reading Firestore `favorites` collection...');
		const snapshot = await firestore.collection('favorites').get();
		console.log(`Read ${snapshot.docs.length} Firestore favorites document(s).`);

		// --- Safety guard ---
		let newestFirestoreUpdateTime = new Date(0);
		for (const doc of snapshot.docs) {
			const t = doc.updateTime.toDate();
			if (t > newestFirestoreUpdateTime) newestFirestoreUpdateTime = t;
		}

		const newestMongoFavorite = await imagesCollection.findOne(
			{'favorites.updatedAt': {$exists: true}},
			{sort: {'favorites.updatedAt': -1}, projection: {'favorites.updatedAt': 1}},
		);
		const newestMongoUpdatedAt: Date | undefined = newestMongoFavorite?.favorites?.updatedAt;

		if (!FORCE && newestMongoUpdatedAt && newestMongoUpdatedAt > newestFirestoreUpdateTime) {
			console.error(
				`\nABORTING: MongoDB's newest favorites.updatedAt (${newestMongoUpdatedAt.toISOString()}) ` +
					`is newer than Firestore's newest favorites updateTime (${newestFirestoreUpdateTime.toISOString()}).\n` +
					'This means the app has likely already cut over to writing favorites directly to MongoDB. ' +
					'Re-running this migration would overwrite post-cutover data with stale Firestore state.\n' +
					'Pass --force to override this guard if you are certain this is intended.',
			);
			process.exitCode = 1;
			return;
		}

		// --- Resolve IDs ---
		const hexIds: string[] = [];
		const stringIds: string[] = [];
		for (const doc of snapshot.docs) {
			if (OBJECT_ID_RE.test(doc.id)) {
				hexIds.push(doc.id);
			} else {
				stringIds.push(doc.id);
			}
		}

		const objectIdMatches = new Set<string>();
		if (hexIds.length > 0) {
			const found = await imagesCollection
				.find({_id: {$in: hexIds.map((id) => new ObjectId(id))}}, {projection: {_id: 1}})
				.toArray();
			for (const doc of found) objectIdMatches.add((doc._id as ObjectId).toHexString());
		}

		const stringMatches = new Set<string>();
		const fallbackCandidates = [...stringIds, ...hexIds.filter((id) => !objectIdMatches.has(id))];
		if (fallbackCandidates.length > 0) {
			const found = await imagesCollection
				.find({_id: {$in: fallbackCandidates}}, {projection: {_id: 1}})
				.toArray();
			for (const doc of found) stringMatches.add(doc._id as string);
		}

		// --- Build resolved favorites ---
		const resolved: ResolvedFavorite[] = [];
		const unmatched: string[] = [];
		let sourceFavoritedAt = 0;
		let sourceMigratedAt = 0;
		let sourceCreateTime = 0;

		for (const doc of snapshot.docs) {
			const data = doc.data();
			const isFavorited: boolean = data.isFavorited === true;
			const categories: string[] = Array.isArray(data.categories) ? data.categories : [];

			let mongoId: string | ObjectId | null = null;
			let idKind: 'objectId' | 'string' = 'string';
			if (objectIdMatches.has(doc.id)) {
				mongoId = new ObjectId(doc.id);
				idKind = 'objectId';
			} else if (stringMatches.has(doc.id)) {
				mongoId = doc.id;
				idKind = 'string';
			}

			if (mongoId === null) {
				unmatched.push(doc.id);
				continue;
			}

			let favoritedAt: Date | null = null;
			let favoritedAtSource: ResolvedFavorite['favoritedAtSource'] = 'createTime';
			if (isFavorited) {
				if (data.favoritedAt?.toDate) {
					favoritedAt = data.favoritedAt.toDate();
					favoritedAtSource = 'favoritedAt';
					sourceFavoritedAt++;
				} else if (data.migratedAt?.toDate) {
					favoritedAt = data.migratedAt.toDate();
					favoritedAtSource = 'migratedAt';
					sourceMigratedAt++;
				} else {
					favoritedAt = doc.createTime.toDate();
					favoritedAtSource = 'createTime';
					sourceCreateTime++;
				}
			}

			resolved.push({
				firestoreId: doc.id,
				mongoId,
				idKind,
				favoritedAtSource,
				favorites: {
					isFavorited,
					categories,
					favoritedAt,
					updatedAt: doc.updateTime.toDate(),
				},
			});
		}

		console.log(`\nResolved as ObjectId _id: ${resolved.filter((r) => r.idKind === 'objectId').length}`);
		console.log(`Resolved as string   _id: ${resolved.filter((r) => r.idKind === 'string').length}`);
		console.log(`Unmatched (skipped):      ${unmatched.length}${unmatched.length > 0 ? `   ${JSON.stringify(unmatched)}` : ''}`);
		console.log(
			`favoritedAt source: favoritedAt ${sourceFavoritedAt} / migratedAt ${sourceMigratedAt} / createTime ${sourceCreateTime}`,
		);

		if (DRY_RUN) {
			console.log('\n[DRY RUN] Preview of first 10 documents:');
			for (const r of resolved.slice(0, 10)) {
				console.log(`  ${r.firestoreId} (${r.idKind}) -> ${JSON.stringify(r.favorites)}`);
			}
		} else if (resolved.length > 0) {
			console.log(`\nWriting ${resolved.length} document(s) to MongoDB...`);
			for (let i = 0; i < resolved.length; i += WRITE_CHUNK) {
				const chunk = resolved.slice(i, i + WRITE_CHUNK);
				const ops: AnyBulkWriteOperation<ImageDoc>[] = chunk.map((r) => ({
					updateOne: {
						filter: {_id: r.mongoId},
						update: {$set: {favorites: r.favorites}},
						upsert: false,
					},
				}));
				const result = await imagesCollection.bulkWrite(ops, {ordered: false});
				console.log(`  Wrote ${Math.min(i + WRITE_CHUNK, resolved.length)} / ${resolved.length} (modified: ${result.modifiedCount})`);
			}
		}

		// --- Prune orphans ---
		const allResolvedMongoIds = resolved.map((r) => r.mongoId);
		const orphanCount = await imagesCollection.countDocuments({
			favorites: {$exists: true},
			_id: {$nin: allResolvedMongoIds},
		});
		console.log(`\nOrphan favorites subdocs in Mongo (no Firestore counterpart): ${orphanCount}`);

		if (PRUNE && !DRY_RUN && orphanCount > 0) {
			const pruneResult = await imagesCollection.updateMany(
				{favorites: {$exists: true}, _id: {$nin: allResolvedMongoIds}},
				{$unset: {favorites: ''}},
			);
			console.log(`Pruned ${pruneResult.modifiedCount} orphan favorites subdoc(s).`);
		} else if (orphanCount > 0) {
			console.log('(use --prune to clear them)');
		}

		console.log('\n--- Report ---');
		console.log(`Firestore favorites read:      ${snapshot.docs.length}  (isFavorited=true: ${resolved.filter((r) => r.favorites.isFavorited).length})`);
		console.log(`Resolved as ObjectId _id:      ${resolved.filter((r) => r.idKind === 'objectId').length}`);
		console.log(`Resolved as string   _id:      ${resolved.filter((r) => r.idKind === 'string').length}`);
		console.log(`Unmatched (skipped):           ${unmatched.length}`);
		console.log(`Orphan favorites subdocs:      ${orphanCount}${PRUNE ? ' (pruned)' : ' (use --prune to clear)'}`);
		console.log(`favoritedAt source: favoritedAt ${sourceFavoritedAt} / migratedAt ${sourceMigratedAt} / createTime ${sourceCreateTime}`);

		if (DRY_RUN) {
			console.log('\n[DRY RUN] No changes were written.');
		} else {
			console.log('\nEnsuring indexes...');
			await ensureIndexes(db);
		}
	} finally {
		await closeDb();
	}
};

main().catch((error) => {
	console.error('Fatal error:', error);
	process.exitCode = 1;
});
