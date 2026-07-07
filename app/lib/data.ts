/**
 * Bundled dataset loader.
 * In Phase 5 these require() calls will point to the generated /data/dist assets.
 * For Phase 0 they return empty arrays so the app boots.
 */

export interface House {
  id: string;
  village_id: string;
  sheet_id: string;
  map_number: number;
  names: string[];
  names_normalized: string[];
  page_pos: { x: number; y: number };
  image_pos: { x: number; y: number };
  lat: number | null;
  lng: number | null;
  source_pdf: string;
}

export interface Village {
  id: string;
  name: string;
  district: string;
  sheet_ids: string[];
  centroid: { lat: number; lng: number } | null;
}

// TODO Phase 5: replace with require("../../data/dist/houses.json")
export const HOUSES: House[] = [];
// TODO Phase 5: replace with require("../../data/dist/villages.json")
export const VILLAGES: Village[] = [];

export const VILLAGE_BY_ID: Record<string, Village> = Object.fromEntries(
  VILLAGES.map((v) => [v.id, v])
);

export const HOUSE_BY_ID: Record<string, House> = Object.fromEntries(
  HOUSES.map((h) => [h.id, h])
);
