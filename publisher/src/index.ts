// eslint-disable-next-line import/no-namespace
import * as firebase from 'firebase-admin';
import {getFunctions} from 'firebase-admin/functions';
import {info} from 'firebase-functions/logger';
import {onDocumentCreated} from 'firebase-functions/v2/firestore';

const app = firebase.initializeApp();

export const onArtworkCreated = onDocumentCreated('artworks/{artworkId}', async (event) => {
	info('Artwork created', {structuredData: true});
	// @ts-expect-error: hoge
	info(await app.INTERNAL.getToken());
	const queue = getFunctions().taskQueue('executeTestFunction');
	info(queue);
	const result = await queue.enqueue({
		artworkId: event.params.artworkId,
	}, {
		scheduleDelaySeconds: 0,
		dispatchDeadlineSeconds: 60 * 5,
	});
	info(result);
});
