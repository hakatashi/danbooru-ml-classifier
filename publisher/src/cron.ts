import 'dotenv/config';
import {schedule} from 'node-cron';
import {fetchDanbooruDailyRankings} from './danbooru';
import {closeDb} from './db';
import {fetchGelbooruDailyImages} from './gelbooru';
import {fetchPixivDailyRankings} from './pixiv';
import {fetchSankakuDailyImages} from './sankaku';

const jobs = {
	pixiv: fetchPixivDailyRankings,
	danbooru: fetchDanbooruDailyRankings,
	gelbooru: fetchGelbooruDailyImages,
	sankaku: fetchSankakuDailyImages,
} as const;

type JobName = keyof typeof jobs;

const runJob = async (name: string, fn: () => Promise<void>): Promise<void> => {
	console.log(`[${name}] Starting job at ${new Date().toISOString()}`);
	try {
		await fn();
		console.log(`[${name}] Job completed at ${new Date().toISOString()}`);
	} catch (error) {
		console.error(`[${name}] Job failed:`, error);
	}
};

const runAllJobs = async (): Promise<void> => {
	for (const [name, fn] of Object.entries(jobs)) {
		await runJob(name, fn);
	}
};

const args = process.argv.slice(2);
const runIndex = args.indexOf('--run');

if (runIndex === -1) {
	// Schedule daily at 15:00 Asia/Tokyo
	console.log('Starting scheduler (daily at 15:00 Asia/Tokyo)...');
	console.log('Use --run [pixiv|danbooru|gelbooru|sankaku|all] to run immediately');

	schedule('0 15 * * *', () => {
		runAllJobs().catch((error) => {
			console.error('Fatal error in scheduled run:', error);
		});
	}, {timezone: 'Asia/Tokyo'});
} else {
	const target = args[runIndex + 1] ?? 'all';

	(async () => {
		if (target === 'all') {
			await runAllJobs();
		} else if (Object.hasOwn(jobs, target)) {
			await runJob(target, jobs[target as JobName]);
		} else {
			console.error(`Unknown job: ${target}. Available: ${Object.keys(jobs).join(', ')}, all`);
			// eslint-disable-next-line no-process-exit, node/no-process-exit
			process.exit(1);
		}
		await closeDb();
	})().catch((error) => {
		console.error('Fatal error:', error);
		// eslint-disable-next-line no-process-exit, node/no-process-exit
		process.exit(1);
	});
}
