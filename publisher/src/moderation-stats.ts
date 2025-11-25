import {getFirestore, FieldValue} from 'firebase-admin/firestore';
import {logger} from 'firebase-functions';
import {onDocumentWritten} from 'firebase-functions/v2/firestore';

/**
 * Updates moderation statistics when an image's moderation data changes.
 * Tracks count and sum of moderation results per provider.
 */
export const updateModerationStats = onDocumentWritten('images/{imageId}', async (event) => {
	const change = event.data;
	if (!change) {
		return;
	}
	const db = getFirestore();
	const statsRef = db.collection('moderationStats');

	// Get before and after moderation data
	const beforeData = change.before.exists ? change.before.data() : null;
	const afterData = change.after.exists ? change.after.data() : null;

	const beforeModerations = beforeData?.moderations || {};
	const afterModerations = afterData?.moderations || {};

	// Get all providers from both before and after
	const allProviders = new Set([
		...Object.keys(beforeModerations),
		...Object.keys(afterModerations),
	]);

	// Update statistics for each provider
	const batch = db.batch();
	let hasChanges = false;

	for (const provider of allProviders) {
		const beforeResult = beforeModerations[provider]?.result;
		const afterResult = afterModerations[provider]?.result;

		// Skip if both are null or undefined
		if (beforeResult === null && afterResult === null) {
			continue;
		}
		if (beforeResult === undefined && afterResult === undefined) {
			continue;
		}

		const providerStatsRef = statsRef.doc(provider);

		// Calculate the increment values
		let countIncrement = 0;
		let sumIncrement = 0;

		// Handle removal of result
		if (beforeResult !== null && beforeResult !== undefined &&
			(afterResult === null || afterResult === undefined)) {
			countIncrement = -1;
			sumIncrement = -beforeResult;
		} else if ((beforeResult === null || beforeResult === undefined) &&
			afterResult !== null && afterResult !== undefined) {
			// Handle addition of result
			countIncrement = 1;
			sumIncrement = afterResult;
		} else if (beforeResult !== null && beforeResult !== undefined &&
			afterResult !== null && afterResult !== undefined &&
			beforeResult !== afterResult) {
			// Handle update of result
			sumIncrement = afterResult - beforeResult;
		}

		// Only update if there are actual changes
		if (countIncrement !== 0 || sumIncrement !== 0) {
			hasChanges = true;
			batch.set(providerStatsRef, {
				count: FieldValue.increment(countIncrement),
				sum: FieldValue.increment(sumIncrement),
				updatedAt: FieldValue.serverTimestamp(),
			}, {merge: true});

			logger.info(`Updating stats for provider ${provider}`, {
				imageId: event.params?.imageId,
				countIncrement,
				sumIncrement,
			});
		}
	}

	if (hasChanges) {
		await batch.commit();
	}
});
