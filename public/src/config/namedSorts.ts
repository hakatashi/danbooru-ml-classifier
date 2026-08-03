export interface NamedSort {
	name: string;
	symbol: string;
	field: string;
	modelKey: string;
}

export const NAMED_SORTS: readonly NamedSort[] = [
	{
		name: 'Aries',
		symbol: '♈',
		field: 'inferences.eva02_twitter_elkan_noto_joblib.score',
		modelKey: 'eva02_twitter_elkan_noto_joblib',
	},
	{
		name: 'Taurus',
		symbol: '♉',
		field: 'inferences.deepdanbooru_twitter_biased_svm_joblib.score',
		modelKey: 'deepdanbooru_twitter_biased_svm_joblib',
	},
	{
		name: 'Gemini',
		symbol: '♊',
		field: 'inferences.eva02_pixiv_private_nnpu_joblib.score',
		modelKey: 'eva02_pixiv_private_nnpu_joblib',
	},
	{
		name: 'Cancer',
		symbol: '♋',
		field: 'inferences.pixai_pixiv_private_elkan_noto_joblib.score',
		modelKey: 'pixai_pixiv_private_elkan_noto_joblib',
	},
	{
		name: 'Leo',
		symbol: '♌',
		field: 'inferences.deepdanbooru_pixiv_private_elkan_noto_joblib.score',
		modelKey: 'deepdanbooru_pixiv_private_elkan_noto_joblib',
	},
	{
		// Rank-average of the 9 pixiv_private models (3 features x 3 methods),
		// materialized server-side by worker/compute_ensembles.py. Strongest
		// within-page AUC on pages 0-1 (the pages actually browsed day to day).
		name: 'Virgo',
		symbol: '♍',
		field: 'inferences.ensemble_virgo_v1.score',
		modelKey: 'ensemble_virgo_v1',
	},
	{
		// Rank-average of the top 5 models across feature types (incl. a
		// twitter-trained model), materialized alongside Virgo. Strongest
		// within-page AUC on deeper pages; offline metrics don't settle which
		// of the two actually surfaces better images, hence both as tabs.
		name: 'Libra',
		symbol: '♎',
		field: 'inferences.ensemble_libra_v1.score',
		modelKey: 'ensemble_libra_v1',
	},
] as const;

export const NAMED_SORT_FIELDS: ReadonlySet<string> = new Set(
	NAMED_SORTS.map((s) => s.field),
);

// ── Unviewed-pages tracking ──────────────────────────────────────────────────
// Which sort field / page range / date window the /daily/unviewed listing
// audits. Fixed to Gemini (the sort actually browsed day to day) rather than
// derived from NAMED_SORTS, so switching the primary sort later doesn't
// silently change what "unviewed" means.

export const TRACKED_SORT_FIELD =
	NAMED_SORTS.find((s) => s.name === 'Gemini')?.field ?? NAMED_SORTS[0].field;
export const TRACKED_PAGES = 3;
export const TRACKED_DAYS = 30;
