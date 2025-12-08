export interface SortOption {
	field: string;
	direction: 'asc' | 'desc';
}

export interface RatingFilter {
	provider: 'joycaption' | 'minicpm';
	min: number | null;
	max: number | null;
}

export interface AgeFilter {
	provider: 'joycaption' | 'minicpm';
	min: number | null;
	max: number | null;
}

export interface TwitterUserFilter {
	userId: string | null;
}

export interface PixAITagFilter {
	tag: string;
	category: 'character' | 'feature' | 'ip';
	confidence: 'high' | 'medium' | 'low';
}

export interface FiltersState {
	rating: RatingFilter;
	age: AgeFilter;
	twitterUser: TwitterUserFilter;
	pixaiTag: PixAITagFilter | null;
}
