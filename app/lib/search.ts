/**
 * Fuzzy search over house names and village names using Fuse.js (spec §5.7).
 */

import Fuse from "fuse.js";
import { HOUSES, VILLAGES, House, Village } from "./data";

export interface SearchResult {
  house: House;
  village: Village | undefined;
  score: number;
}

type SearchRecord = {
  house: House;
  village: Village | undefined;
  // denormalized fields for Fuse
  name_primary: string;
  village_name: string;
};

const records: SearchRecord[] = HOUSES.map((h) => {
  const village = VILLAGES.find((v) => v.id === h.village_id);
  return {
    house: h,
    village,
    name_primary: h.names_normalized[0] ?? "",
    village_name: village?.name.toLowerCase() ?? "",
  };
});

const fuse = new Fuse(records, {
  keys: [
    { name: "name_primary", weight: 0.8 },
    { name: "village_name", weight: 0.2 },
  ],
  threshold: 0.4,
  includeScore: true,
  useExtendedSearch: false,
  ignoreLocation: true,
});

export function search(query: string): SearchResult[] {
  if (!query.trim()) return [];
  const normalised = query
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return fuse.search(normalised).map((r) => ({
    house: r.item.house,
    village: r.item.village,
    score: r.score ?? 1,
  }));
}
