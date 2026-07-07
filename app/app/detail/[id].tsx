/**
 * Detail screen (spec §5.7).
 * Shows the sheet image with a highlight ring at image_pos,
 * and a Navigate button that opens native maps.
 */

import { useState } from "react";
import {
  Alert,
  Dimensions,
  Image,
  Linking,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { useLocalSearchParams, useNavigation } from "expo-router";
import { useEffect } from "react";
import { HOUSE_BY_ID, VILLAGE_BY_ID } from "../../lib/data";
import { openInMaps } from "../../lib/navigate";

export default function DetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const navigation = useNavigation();

  const house = id ? HOUSE_BY_ID[id] : undefined;
  const village = house ? VILLAGE_BY_ID[house.village_id] : undefined;

  useEffect(() => {
    if (house) navigation.setOptions({ title: house.names[0] });
  }, [house, navigation]);

  if (!house) {
    return (
      <View style={styles.center}>
        <Text>House not found.</Text>
      </View>
    );
  }

  const hasCoords = house.lat !== null && house.lng !== null;
  const navLat = hasCoords ? house.lat! : village?.centroid?.lat ?? null;
  const navLng = hasCoords ? house.lng! : village?.centroid?.lng ?? null;

  const handleNavigate = async () => {
    if (navLat === null || navLng === null) {
      Alert.alert("No location", "This house has no coordinates yet.");
      return;
    }
    await openInMaps(navLat, navLng, house.names[0]);
  };

  const handleReport = () => {
    const subject = encodeURIComponent(`Wrong location: ${house.id}`);
    const body = encodeURIComponent(
      `Sheet: ${house.sheet_id}\nHouse: ${house.id}\nNote: `
    );
    Linking.openURL(`mailto:?subject=${subject}&body=${body}`);
  };

  // TODO Phase 5: resolve actual image asset from bundled assets
  // const imageSource = require(`../../data/dist/images/${house.sheet_id}.png`);
  const imageSource = null;
  const imageSize = { w: 1000, h: 1000 }; // placeholder until real asset

  const screenWidth = Dimensions.get("window").width;
  const displayScale = imageSize.w > 0 ? screenWidth / imageSize.w : 1;
  const ringX = house.image_pos.x * displayScale;
  const ringY = house.image_pos.y * displayScale;

  return (
    <ScrollView style={styles.container}>
      <View style={styles.imageWrapper}>
        {imageSource ? (
          <>
            <Image
              source={imageSource}
              style={{ width: screenWidth, height: imageSize.h * displayScale }}
              resizeMode="contain"
            />
            <View
              style={[
                styles.ring,
                { left: ringX - 16, top: ringY - 16 },
              ]}
            />
          </>
        ) : (
          <View style={styles.imagePlaceholder}>
            <Text style={styles.placeholderText}>
              Map image not yet bundled (Phase 5)
            </Text>
          </View>
        )}
      </View>

      <View style={styles.info}>
        <Text style={styles.houseName}>{house.names.join(" / ")}</Text>
        <Text style={styles.villageName}>{village?.name ?? house.village_id}</Text>

        {!hasCoords && (
          <Text style={styles.approximate}>
            Exact location not yet confirmed — navigation targets the village centre.
            Use the drawing above for the final approach.
          </Text>
        )}

        <TouchableOpacity
          style={[styles.button, !navLat && styles.buttonDisabled]}
          onPress={handleNavigate}
          disabled={navLat === null}
        >
          <Text style={styles.buttonText}>Navigate</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.reportButton} onPress={handleReport}>
          <Text style={styles.reportText}>Report wrong location</Text>
        </TouchableOpacity>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#fff" },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  imageWrapper: { position: "relative" },
  imagePlaceholder: {
    height: 200,
    backgroundColor: "#eee",
    alignItems: "center",
    justifyContent: "center",
    margin: 12,
    borderRadius: 8,
  },
  placeholderText: { color: "#999" },
  ring: {
    position: "absolute",
    width: 32,
    height: 32,
    borderRadius: 16,
    borderWidth: 3,
    borderColor: "#e63946",
    backgroundColor: "transparent",
  },
  info: { padding: 16 },
  houseName: { fontSize: 22, fontWeight: "700" },
  villageName: { fontSize: 16, color: "#555", marginTop: 4, marginBottom: 12 },
  approximate: {
    backgroundColor: "#fff3cd",
    borderRadius: 6,
    padding: 10,
    fontSize: 13,
    color: "#856404",
    marginBottom: 12,
  },
  button: {
    backgroundColor: "#1d6fa4",
    borderRadius: 8,
    padding: 14,
    alignItems: "center",
    marginBottom: 10,
  },
  buttonDisabled: { backgroundColor: "#999" },
  buttonText: { color: "#fff", fontSize: 16, fontWeight: "600" },
  reportButton: { alignItems: "center", padding: 10 },
  reportText: { color: "#888", fontSize: 13, textDecorationLine: "underline" },
});
