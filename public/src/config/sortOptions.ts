import type {SortOption} from '../types/filters';

export interface SortOptionConfig {
	value: string;
	label: string;
	field: string;
	direction: 'asc' | 'desc';
}

export const sortOptions: SortOptionConfig[] = [
	{
		value: 'joycaption-desc',
		label: 'JoyCaption Rating (High to Low)',
		field: 'moderations.joycaption.result',
		direction: 'desc',
	},
	{
		value: 'joycaption-asc',
		label: 'JoyCaption Rating (Low to High)',
		field: 'moderations.joycaption.result',
		direction: 'asc',
	},
	{
		value: 'minicpm-desc',
		label: 'MiniCPM Rating (High to Low)',
		field: 'moderations.minicpm.result',
		direction: 'desc',
	},
	{
		value: 'minicpm-asc',
		label: 'MiniCPM Rating (Low to High)',
		field: 'moderations.minicpm.result',
		direction: 'asc',
	},
	{
		value: 'joycaption-age-desc',
		label: 'JoyCaption Age (High to Low)',
		field: 'ageEstimations.joycaption.main_character_age',
		direction: 'desc',
	},
	{
		value: 'joycaption-age-asc',
		label: 'JoyCaption Age (Low to High)',
		field: 'ageEstimations.joycaption.main_character_age',
		direction: 'asc',
	},
	{
		value: 'minicpm-age-desc',
		label: 'MiniCPM Age (High to Low)',
		field: 'ageEstimations.minicpm.main_character_age',
		direction: 'desc',
	},
	{
		value: 'minicpm-age-asc',
		label: 'MiniCPM Age (Low to High)',
		field: 'ageEstimations.minicpm.main_character_age',
		direction: 'asc',
	},
	{
		value: 'qwen3-age-desc',
		label: 'Qwen3 Age (High to Low)',
		field: 'ageEstimations.qwen3.main_character_age',
		direction: 'desc',
	},
	{
		value: 'qwen3-age-asc',
		label: 'Qwen3 Age (Low to High)',
		field: 'ageEstimations.qwen3.main_character_age',
		direction: 'asc',
	},
	{
		value: 'joycaption-created-desc',
		label: 'JoyCaption Created (Newest First)',
		field: 'captions.joycaption.metadata.createdAt',
		direction: 'desc',
	},
	{
		value: 'joycaption-created-asc',
		label: 'JoyCaption Created (Oldest First)',
		field: 'captions.joycaption.metadata.createdAt',
		direction: 'asc',
	},
	{
		value: 'minicpm-created-desc',
		label: 'MiniCPM Created (Newest First)',
		field: 'captions.minicpm.metadata.createdAt',
		direction: 'desc',
	},
	{
		value: 'minicpm-created-asc',
		label: 'MiniCPM Created (Oldest First)',
		field: 'captions.minicpm.metadata.createdAt',
		direction: 'asc',
	},
];

export const sortOptionsMap: Record<string, SortOption> = sortOptions.reduce(
	(acc, option) => {
		acc[option.value] = {field: option.field, direction: option.direction};
		return acc;
	},
	{} as Record<string, SortOption>,
);

export function getSortOption(value: string): SortOption {
	return (
		sortOptionsMap[value] || {
			field: 'captions.minicpm.metadata.createdAt',
			direction: 'desc',
		}
	);
}

export const DEFAULT_SORT = 'minicpm-created-desc';
