/**
 * Search screen (spec §5.7).
 * Phase 0: renders with empty data; wired to real data in Phase 5.
 */

import { useState } from "react";
import {
  FlatList,
  Image,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { useRouter } from "expo-router";
import { search, SearchResult } from "../lib/search";

export default function SearchScreen() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const results = search(query);

  return (
    <View style={styles.container}>
      <TextInput
        style={styles.input}
        placeholder="House name or village..."
        value={query}
        onChangeText={setQuery}
        autoCorrect={false}
        clearButtonMode="while-editing"
      />
      {results.length === 0 && query.length > 0 && (
        <Text style={styles.empty}>No results</Text>
      )}
      <FlatList
        data={results}
        keyExtractor={(r) => r.house.id}
        renderItem={({ item }) => (
          <ResultCard
            result={item}
            onPress={() => router.push(`/detail/${item.house.id}`)}
          />
        )}
      />
    </View>
  );
}

function ResultCard({
  result,
  onPress,
}: {
  result: SearchResult;
  onPress: () => void;
}) {
  const { house, village } = result;
  // TODO Phase 5: resolve actual image asset
  const imageSource = null;

  return (
    <TouchableOpacity style={styles.card} onPress={onPress}>
      {imageSource && (
        <Image source={imageSource} style={styles.thumbnail} />
      )}
      <View style={styles.cardText}>
        <Text style={styles.houseName}>{house.names[0]}</Text>
        <Text style={styles.villageName}>{village?.name ?? house.village_id}</Text>
      </View>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 12, backgroundColor: "#fff" },
  input: {
    borderWidth: 1,
    borderColor: "#ccc",
    borderRadius: 8,
    padding: 10,
    fontSize: 16,
    marginBottom: 8,
  },
  empty: { color: "#888", textAlign: "center", marginTop: 24 },
  card: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: "#ddd",
  },
  thumbnail: { width: 56, height: 56, borderRadius: 4, marginRight: 12 },
  cardText: { flex: 1 },
  houseName: { fontSize: 16, fontWeight: "600" },
  villageName: { fontSize: 13, color: "#666", marginTop: 2 },
});
