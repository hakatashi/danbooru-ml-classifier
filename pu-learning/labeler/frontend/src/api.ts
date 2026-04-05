import type { FilterType, ImageItem, StatusData } from './types';

interface ImagesResponse {
  total: number;
  offset: number;
  limit: number;
  items: ImageItem[];
}

interface LabelResponse {
  ok: boolean;
  labeled?: number;
  total?: number;
  error?: string;
}

interface RatingResponse {
  ok: boolean;
  rating?: number;
  error?: string;
}

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json() as Promise<T>;
}

export async function fetchStatus(): Promise<StatusData> {
  const r = await fetch('/api/status');
  return r.json() as Promise<StatusData>;
}

export async function fetchImages(
  filter: FilterType,
  offset = 0,
  limit = 5000,
): Promise<ImagesResponse> {
  const q = new URLSearchParams({
    filter,
    offset: String(offset),
    limit: String(limit),
  });
  const r = await fetch(`/api/images?${q}`);
  return r.json() as Promise<ImagesResponse>;
}

export function imageUrl(path: string): string {
  return `/api/image?path=${encodeURIComponent(path)}`;
}

export function thumbnailUrl(path: string): string {
  return `/api/thumbnail?path=${encodeURIComponent(path)}`;
}

export function postLabel(path: string, label: string, rating = 1): Promise<LabelResponse> {
  return postJSON('/api/label', { path, label, rating });
}

export function postUnlabel(path: string): Promise<{ ok: boolean }> {
  return postJSON('/api/unlabel', { path });
}

export function postRating(path: string, rating: number): Promise<RatingResponse> {
  return postJSON('/api/rating', { path, rating });
}
