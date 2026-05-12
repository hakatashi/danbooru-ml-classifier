export function useScoreDisplay() {
	function getScoreBarWidth(value: number): string {
		return `${Math.max(0, Math.min(100, value * 100)).toFixed(1)}%`;
	}

	function getScoreColorClass(value: number): string {
		if (value >= 0.8) return 'bg-purple-500';
		if (value >= 0.6) return 'bg-blue-500';
		if (value >= 0.4) return 'bg-green-500';
		if (value >= 0.2) return 'bg-yellow-500';
		return 'bg-gray-400';
	}

	/** Bar width based on rank: rank 1 → 100%, rank=total → ~0% */
	function getRankBarWidth(rank: number | null, total: number): string {
		if (rank === null || total === 0) return '0%';
		return `${(((total - rank + 1) / total) ** 15 * 100).toFixed(1)}%`;
	}

	/** Color based on rank percentile (lower rank = better) */
	function getRankColorClass(rank: number | null, total: number): string {
		if (rank === null || total === 0) return 'bg-gray-400';
		const pct = 1 - (1 - rank / total) ** 15;
		if (pct <= 0.2) return 'bg-purple-500';
		if (pct <= 0.4) return 'bg-blue-500';
		if (pct <= 0.6) return 'bg-green-500';
		if (pct <= 0.8) return 'bg-yellow-500';
		return 'bg-gray-400';
	}

	function getRatingColorClass(rating: number | null): string {
		if (rating === null) return 'bg-gray-500';
		if (rating <= 2) return 'bg-green-500';
		if (rating <= 4) return 'bg-lime-500';
		if (rating <= 6) return 'bg-orange-500';
		if (rating <= 8) return 'bg-red-500';
		return 'bg-purple-500';
	}

	function getRatingLabel(rating: number | null): string {
		if (rating === null) return 'Unknown';
		if (rating <= 2) return 'Safe';
		if (rating <= 4) return 'Slightly Suggestive';
		if (rating <= 6) return 'Sensitive';
		if (rating <= 8) return 'Adult';
		return 'Explicit';
	}

	return {
		getScoreBarWidth,
		getScoreColorClass,
		getRankBarWidth,
		getRankColorClass,
		getRatingColorClass,
		getRatingLabel,
	};
}
