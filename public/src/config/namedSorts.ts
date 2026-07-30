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
] as const;

export const NAMED_SORT_FIELDS: ReadonlySet<string> = new Set(
	NAMED_SORTS.map((s) => s.field),
);
